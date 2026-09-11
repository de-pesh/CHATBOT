import json
import logging
from typing import Any, Dict, Optional

import redis.exceptions as redis_exceptions

from app.config import Settings
from app.controllers.llm_controller import llm_handler
from app.services.redis_service import RedisClusterService
from app.services.database_service import DatabaseService
from app.services.llm_service import BaseLLMService
from app.core.stored_procedures import StoredProcedures
from app.utils.general_utils import get_uuid

logger = logging.getLogger(__name__)

PENDING_RESULT_STATUSES = {"initiated", "queued", "processing", "running", "in-progress", "in_progress"}


def _normalize_result_status(value: Any) -> str:
    status_value = str(value or "").strip().lower().replace("_", "-")
    if status_value == "inprogress":
        return "in-progress"
    return status_value


def _derive_compliance(adherence: Any) -> Optional[str]:
    """Deterministically map an adherence score to a compliance label.

    Thresholds (inclusive lower bound):
        ≥ 85  → Compliant
        ≥ 50  → Partially Compliant
        < 50  → Non-Compliant
        None  → None (no data yet)
    """
    if adherence is None:
        return None
    try:
        score = float(adherence)
    except (TypeError, ValueError):
        return None
    if score >= 85:
        return "Compliant"
    if score >= 50:
        return "Partially Compliant"
    return "Non-Compliant"


async def _transition_assessment_to_run_review_if_complete(
    db_service: DatabaseService,
    assessment_id: Any,
    assessment_version_id: Any,
) -> None:
    """
    Moves assessment status to pending_run_review when all result_by_label rows
    for the run are terminal (i.e., no pending rows remain).
    """
    if not assessment_id or not assessment_version_id:
        return

    all_rows_res = await db_service.call_procedure(
        StoredProcedures.FILTER_RESULTS_BY_LABEL,
        [None, assessment_version_id, None, None, None, True, 1, 5000],
    )
    all_rows = all_rows_res[0] if all_rows_res and all_rows_res[0] else []
    has_pending = any(
        _normalize_result_status(row.get("status")) in PENDING_RESULT_STATUSES
        for row in all_rows
    )

    if has_pending:
        return

    assessment_res = await db_service.call_procedure(
        StoredProcedures.FILTER_ASSESSMENTS,
        [assessment_id, None, None, None, True, 1, 1],
    )
    if not assessment_res or not assessment_res[0]:
        logger.warning(
            "Could not transition assessment %s to pending_run_review: assessment not found.",
            assessment_id,
        )
        return

    current_status = str(assessment_res[0][0].get("status") or "").strip().lower()
    if current_status != "assessment_in_progress":
        return

    await db_service.call_procedure(
        StoredProcedures.UPSERT_ASSESSMENT,
        [assessment_id, None, None, "pending_run_review", None, None, "SYSTEM"],
    )
    logger.info(
        "Assessment %s transitioned to pending_run_review for version %s.",
        assessment_id,
        assessment_version_id,
    )

# Message processing logic
async def process_message(
    payload: Dict[str, Any],
    operation_id: str,
    # graph_queue_service: RedisClusterService,
    analysis_queue_service: RedisClusterService,
    db_service: DatabaseService,
    llm_service: BaseLLMService,
    settings: Settings
):
    """
    Processes messages from the LLM queue for criteria generation and dynamic assessment.
    """
    logger.info(f"[Processor {operation_id}] Processing LLM task.")
    logger.debug(f"[Processor {operation_id}] Payload: {payload}")

    # --- Standard Fields ---
    action_type_str = payload.get("action_type")
    framework_id = payload.get("framework_id") or payload.get("standard_id")
    version_id = payload.get("version_id")
    node_id = payload.get("node_id")
    query = payload.get("prompt")

    # --- Input Validation ---
    if action_type_str is None:
        raise ValueError(f"Task {operation_id}: Action Type is missing.")
    try:
        action_type = int(action_type_str)
    except (ValueError, TypeError):
        raise ValueError(f"Task {operation_id}: Invalid Action Type '{action_type_str}'.")

    # if not db_name:
    #     raise ValueError(f"Task {operation_id}: DB Name is missing.")
    # if not framework_id:
    #     raise ValueError(f"Task {operation_id}: Framework ID is missing.")
    if not node_id:
        raise ValueError(f"Task {operation_id}: Node ID is missing.")
    if not query:
        raise ValueError(f"Task {operation_id}: Query is missing.")

    try:
        # --- Call the LLM handler to get the core response ---
        logger.debug(f"[Processor {operation_id}] Calling LLM handler for action type {action_type}.")
        llm_response = await llm_handler(
            llm_service=llm_service, # <-- Pass the service instance
            query=query,
            model_params={
                "model_name": settings.MODEL_NAME, "temperature": 0.7, "top_p": 0.95,
                "presence_penalty": 0, "frequency_penalty": 0
            }
        )
        logger.debug(f"[Processor {operation_id}] LLM response received.")

        # --- Action Dispatcher ---
        # Initialize the graph queue service *only if needed*.
        if action_type == 0: # Action 0: Generate and send new criteria to the graph worker.
            if not isinstance(llm_response, dict) or "criterias" not in llm_response:
                raise ValueError(f"Task {operation_id}: LLM response for criteria generation is invalid. Expected a dict with a 'criterias' key.")
            
            criteria_list = llm_response.get('criterias', [])
            logger.info(f"[Processor {operation_id}] LLM generated {len(criteria_list)} criteria for node '{node_id}'.")

            # We need to send these to the graph queue.
            messages_sent = 0
            for criteria_text in criteria_list:
                if not isinstance(criteria_text, str) or not criteria_text.strip():
                    logger.warning(f"[Processor {operation_id}] Skipping empty or non-string criteria.")
                    continue

                version_file_id = payload.get("version_file_id")
                if not version_file_id:
                    raise ValueError(f"Task {operation_id}: 'version_file_id' is required in payload for action_type 0.")
                new_criteria_node_id = get_uuid()
                await db_service.call_procedure(
                StoredProcedures.UPSERT_TEMP_NODE_DETAIL,
                [
                    None,
                    version_file_id,
                    new_criteria_node_id,
                    None,
                    "Criteria",
                    criteria_text,
                    None,
                    None,
                    None,
                    node_id,
                    1,
                    "LLM"
                ]
            )
                
            #     # This is the payload for the graph worker to add a new Criteria node.
            #     graph_payload = {
            #         "action_type": 1, # 1 = ADD
            #         "db_name": db_name,
            #         "framework_id": framework_id,
            #         "parent_id": node_id,   # The node that requested criteria is the parent.
            #         "node_id": get_uuid(),  # A new, unique ID for the criteria node itself.
            #         "label_type": "Criteria",
            #         "name": "Criteria",
            #         "description": criteria_text,
            #         "created_by": "SYSTEM",
            #         "label_id": "0"
            #     }
            #     await graph_queue_service.send_message(payload=graph_payload)
            #     messages_sent += 1
            # logger.info(f"[Processor {operation_id}] Sent {messages_sent} new criteria to the graph queue.")

        elif action_type == 1:
            logger.info(f"[Processor {operation_id}] Action 1: Processing assessment result for node '{node_id}'.")
            
            # --- Validate payload for action_type 1 ---
            assessment_id = payload.get("assessment_id")
            assessment_version_id = payload.get("assessment_version_id")
            parent_id = payload.get("parent_id")
            stop_at_this_level = payload.get("stop_at_this_level", False)

            if not assessment_id:
                raise ValueError(f"Task {operation_id}: Assessment ID is missing for action_type 1.")
            if not assessment_version_id:
                raise ValueError(f"Task {operation_id}: Assessment Version ID is missing for action_type 1.")
            if not isinstance(llm_response, dict):
                raise ValueError(f"Task {operation_id}: Expected dict from LLM, got {type(llm_response)}.")

            # Determine whether this is a summary (aggregation) node or a leaf
            # criteria node based on the presence of a pre-computed adherence value
            # in the payload.  Summary nodes have adherence computed before the LLM
            # call; leaf criteria nodes rely on the LLM's adherence output.
            pre_computed_adherence = payload.get("pre_computed_adherence")
            is_summary_node = pre_computed_adherence is not None

            if is_summary_node:
                # Aggregation node: use pre-computed average, derive compliance
                # deterministically; take reason and recommendation from LLM.
                final_adherence = pre_computed_adherence
                final_compliance = _derive_compliance(final_adherence)
                final_reason = llm_response.get("reason")
                final_recommendation = llm_response.get("recommendation")
                logger.info(
                    "[Processor %s] Summary node '%s': adherence=%s (pre-computed), compliance=%s (derived).",
                    operation_id, node_id, final_adherence, final_compliance,
                )
            else:
                # Leaf criteria node: adherence from LLM; compliance derived
                # deterministically from that adherence score.
                final_adherence = llm_response.get("adherence")
                final_compliance = _derive_compliance(final_adherence)
                final_reason = llm_response.get("reason")
                final_recommendation = llm_response.get("recommendation")

            # --- 1a. Database Update ---
            logger.info(f"[Processor {operation_id}] Updating database for node '{node_id}' in assessment version '{assessment_version_id}'.")
            
            # Upsert the status entry for this node's assessment
            await db_service.call_procedure(
                StoredProcedures.UPSERT_RESULT_BY_LABEL,
                [None, assessment_version_id, payload.get("control_label"), node_id, 'in-progress', None, 1, 'SYSTEM']
            )

            # Find the ID of the 'results_by_label' entry we just created/updated
            result_label_data = await db_service.call_procedure(
                StoredProcedures.FILTER_RESULTS_BY_LABEL,
                [None, assessment_version_id, None, node_id, None, 1, 1, 1]
            )
            
            if not result_label_data or not result_label_data[0]:
                raise RuntimeError(f"Task {operation_id}: Could not find or create results_by_label entry for node '{node_id}'.")

            result_by_label_id = result_label_data[0][0]['id']

            # Insert the detailed LLM results
            await db_service.call_procedure(
                StoredProcedures.UPSERT_RESULT,
                [
                    None, result_by_label_id, node_id,
                    final_adherence,
                    final_reason,
                    final_recommendation,
                    final_compliance,
                    1, 'SYSTEM'
                ]
            )
            logger.info(f"[Processor {operation_id}] Database update successful for node {node_id}.")

            # --- 1b. Check Stop Condition & Trigger Next Steps ---
            if stop_at_this_level:
                logger.info(f"[Processor {operation_id}] 'stop_at_this_level' is true. Halting assessment for this branch at node '{node_id}'.")
                await db_service.call_procedure(
                StoredProcedures.UPSERT_RESULT_BY_LABEL,
                [result_by_label_id, assessment_version_id, payload.get("control_label"), node_id, 'completed', None, 1, 'SYSTEM']
            )
                await _transition_assessment_to_run_review_if_complete(
                    db_service=db_service,
                    assessment_id=assessment_id,
                    assessment_version_id=assessment_version_id,
                )
            elif not parent_id:
                logger.warning(f"[Processor {operation_id}] Node '{node_id}' has no parent. Assessment for this branch is complete.")
                await db_service.call_procedure(
                    StoredProcedures.UPSERT_RESULT_BY_LABEL,
                    [result_by_label_id, assessment_version_id, payload.get("control_label"), node_id, 'completed', None, 1, 'SYSTEM']
                )
                await _transition_assessment_to_run_review_if_complete(
                    db_service=db_service,
                    assessment_id=assessment_id,
                    assessment_version_id=assessment_version_id,
                )
            else:
                # --- 1c. Redis Session Update & Trigger Parent ---
                lock_value = None
                try:
                    logger.debug(f"[Processor {operation_id}] Acquiring lock and accessing Redis session for assessment '{assessment_id}'.")
                    lock_value = await analysis_queue_service._acquire_lock(assessment_id)
                    session_data = await analysis_queue_service.get_user_session(assessment_id)

                    # Decrement the counter for the parent node
                    if parent_id in session_data.get("nodes_to_process", {}):
                        session_data["nodes_to_process"][parent_id] -= 1
                        new_count = session_data["nodes_to_process"][parent_id]
                        logger.info(f"[Processor {operation_id}] Decremented pending count for parent '{parent_id}' to {new_count}.")

                        # If the counter reaches zero, trigger the parent's assessment
                        if new_count == 0:
                            logger.info(f"[Processor {operation_id}] All children of parent '{parent_id}' are processed. Triggering parent assessment.")
                            assessment_payload = {
                                "analysis_id": assessment_id,
                                "standard_id": framework_id,
                                "version_id": version_id,
                                "node_id_to_summarize": parent_id,
                                "control_label": payload.get("control_label"),
                                "control_id": payload.get("control_id"),
                            }
                            # Send message to assessment service to summarize the children and run the parent
                            await analysis_queue_service.send_message(payload=assessment_payload, priority=50) # Higher priority for aggregation tasks
                        
                        # Update the session regardless; skip lock because this scope already holds it.
                        await analysis_queue_service.add_or_update_session(
                            session_data,
                            int(settings.SESSION_EXPIRY_TIME),
                            skip_lock=True,
                        )

                    else:
                        logger.warning(f"[Processor {operation_id}] Parent ID '{parent_id}' not found in session's 'nodes_to_process' for assessment '{assessment_id}'.")

                finally:
                    if lock_value:
                        await analysis_queue_service._release_lock(assessment_id, lock_value)
                        logger.debug(f"[Processor {operation_id}] Released lock for assessment '{assessment_id}'.")

        elif action_type ==2:
            logger.info(f"[Processor {operation_id}] Action 2: Processing question generation for node '{node_id}'.")
            llm_question = None
            try:
                # Ollama service currently returns parsed JSON (dict).
                # Keep backward compatibility in case a raw JSON string is returned.
                if isinstance(llm_response, dict):
                    data = llm_response
                elif isinstance(llm_response, str):
                    start_index = llm_response.find('{')
                    end_index = llm_response.rfind('}') + 1

                    if start_index == -1 or end_index == 0:
                        raise json.JSONDecodeError("JSON object not found in the LLM response.", llm_response, 0)

                    json_str = llm_response[start_index:end_index]
                    data = json.loads(json_str)
                else:
                    raise ValueError(f"Unsupported LLM response type for action_type 2: {type(llm_response)}")

                llm_question = data.get("question")

                if not llm_question or not isinstance(llm_question, str):
                    raise ValueError("Key 'question' not found or is not a valid string in the parsed LLM response.")
                
                logger.info(f"[Processor {operation_id}] Extracted question for node '{node_id}'.")

            except (json.JSONDecodeError, ValueError) as e:
                logger.error(f"[Processor {operation_id}] Failed to parse question from LLM response for node '{node_id}'. Error: {e}")
                logger.debug(f"[Processor {operation_id}] Raw LLM Response: {llm_response}")
                # Raise the error to stop processing this message
                raise ValueError(f"Invalid LLM response format: {e}")

            # --- 2b. Find the Database Record ID ---
            logger.debug(f"[Processor {operation_id}] Searching for temp_node_detail with node_id '{node_id}'.")
            
            filter_params = [
                None,    # id
                None,    # version_file_id (not filtered — node_id is sufficient to locate the record)
                node_id, # node_id
                None,    # label_id
                None,    # name
                None,    # label_type
                None,    # parent_node_id
                1,       # is_active
                1,       # page_number
                1        # page_size
            ]
            node_data = await db_service.call_procedure(
                StoredProcedures.FILTER_TEMP_NODE_DETAILS,
                filter_params
            )
            
            # The procedure returns two results; the data is in the first one.
            if not node_data or not node_data[0] or not node_data[0][0]:
                raise RuntimeError(f"Task {operation_id}: Could not find an active temp_node_details record for node_id '{node_id}'.")

            # Extract the ID from the result set
            record_id = node_data[0][0]['id']
            logger.info(f"[Processor {operation_id}] Found record with ID '{record_id}' for node '{node_id}'.")

            # --- 2c. Update the Record with the New Question ---
            update_params = [
                record_id,         # id (for UPDATE)
                None,              # version_file_id
                None,              # node_id
                None,              # label_id
                None,              # name
                None,              # description
                None,              # guidelines
                llm_question,      # corresponding_question (the value to update)
                None,              # label_type
                None,              # parent_node_id
                None,              # is_active
                'LLM'           # user_id (as updated_by)
            ]

            await db_service.call_procedure(
                StoredProcedures.UPSERT_TEMP_NODE_DETAIL,
                update_params
            )
            logger.info(f"[Processor {operation_id}] Successfully updated corresponding_question for record ID '{record_id}'.")


        else:
            raise ValueError(f"Task {operation_id}: Invalid action_type '{action_type}'.")

        logger.info(f"[Processor {operation_id}] Successfully finished LLM task.")

    except redis_exceptions.RedisError as e:
        logger.error(f"[Processor {operation_id}] Redis Error: {e}")
        raise
    except ValueError as e:
        logger.error(f"[Processor {operation_id}] Data validation or logic error: {e}")
        raise
    except Exception as e:
        logger.exception(f"[Processor {operation_id}] Unexpected error processing LLM message: {e}")
        raise