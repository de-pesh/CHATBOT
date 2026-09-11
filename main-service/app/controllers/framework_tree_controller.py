import logging
from typing import Optional, List

from fastapi import HTTPException, status

from app.stored_procedures import StoredProcedures
from app.models.framework import FrameworkNode, FrameworkTreeResponse
from app.services.database_service import DatabaseService

logger = logging.getLogger(__name__)

class FrameworkTreeController:
    """
    Controller for getting framework tree.

    This class encapsulates the business logic for retrieving preview tree with changes and also tree from any version.
    """

    def __init__(self, db_service: DatabaseService):
        self.db_service = db_service

    
    async def _create_tree(self, root_id: str, node_mapping: dict, label_config: dict, level: int) -> Optional[FrameworkNode]:
        try:
            if level > 0:
                regular_children = []
                criteria_texts = []
                for child_id in node_mapping[root_id]["children"]:
                    child = node_mapping[child_id]
                    if child.get("label_type") is None:
                        # Criteria node: collect description text instead of recursing
                        if child.get("description"):
                            criteria_texts.append(child["description"])
                    else:
                        child_node = await self._create_tree(child_id, node_mapping, label_config, level - 1)
                        if child_node:
                            regular_children.append(child_node)

                # Fallback: if this is a leaf in the loaded tree but has no criteria,
                # fetch criteria children by parent_node_id to handle cross-version
                # temp-node placement mismatches.
                if not regular_children and not criteria_texts:
                    fallback_criteria = await self.db_service.call_procedure(
                        StoredProcedures.FILTER_TEMP_NODES,
                        [None, None, None, None, None, None, root_id, True, 1, 10000]
                    )
                    if (not fallback_criteria or not fallback_criteria[0]):
                        fallback_criteria = await self.db_service.call_procedure(
                            StoredProcedures.FILTER_NODES,
                            [None, None, None, None, None, None, root_id, True, 1, 10000]
                        )

                    if fallback_criteria and fallback_criteria[0]:
                        seen = set()
                        for child in fallback_criteria[0]:
                            if child.get("label_type") is None and child.get("description"):
                                desc = child.get("description")
                                if desc not in seen:
                                    seen.add(desc)
                                    criteria_texts.append(desc)

                return FrameworkNode(
                    node_id = node_mapping[root_id]["node_id"],
                    label_id = node_mapping[root_id]["label_id"],
                    name = node_mapping[root_id]["name"],
                    description = node_mapping[root_id]["description"],
                    guidelines = node_mapping[root_id]["guidelines"],
                    correspoding_question = node_mapping[root_id]["correspoding_question"] if "correspoding_question" in node_mapping[root_id] else None,
                    label_type = node_mapping[root_id]["label_type"],
                    created_by = node_mapping[root_id]["created_by"],
                    created_on = node_mapping[root_id]["created_on"],
                    updated_by = node_mapping[root_id]["updated_by"],
                    updated_on = node_mapping[root_id]["updated_on"],
                    children = regular_children,
                    criterias = criteria_texts if (criteria_texts and not regular_children) else None
                )
            else:
                return None
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error creating tree for framework: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")


    async def get_framework_tree(self, framework_id: int, version_id: int, start_node_id: Optional[str], level: Optional[int]) -> List[FrameworkTreeResponse]:
        """
        Retrieves framework tree for given version.

        Args:
            framework_id (int): The ID of the framework.
            version_id (int): The ID of the version.
            level Optional(int): The level till which tree is required. 

        Returns:
            FrameworkTreeResponse: The tree for the framework.

        Raises:
            HTTPException: If the tree can not be created.
        """
        try:
            response = []

            if not level:
                level=10000000
            
            framework_configuration = await self.db_service.call_procedure(
                StoredProcedures.FILTER_FRAMEWORK_CONFIGURATIONS,
                [None, framework_id, None, None, True, 1, 100000]
            )
            if not framework_configuration or not framework_configuration[0]:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch framework configuration.")

            framework_details = await self.db_service.call_procedure(
                StoredProcedures.FILTER_FRAMEWORKS,
                [framework_id, None, True, 1, 1]
            )
            if not framework_details or not framework_details[0]:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Framework not found.")

            framework_name = framework_details[0][0]["name"]
            
            label_config = {}
            for config in framework_configuration[0]:
                label_config[config.get("id")] = config.get("name")

            start_version_file_id = None
            if start_node_id:
                # Prefer temp-node lookup first; fall back to permanent nodes.
                check_node = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_TEMP_NODES,
                    [None, None, start_node_id, None, None, None, None, True, 1, 1]
                )
                if not check_node or not check_node[0]:
                    check_node = await self.db_service.call_procedure(
                        StoredProcedures.FILTER_NODES,
                        [None, None, start_node_id, None, None, None, None, True, 1, 1]
                    )
                if not check_node or not check_node[0]:
                    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to find start node.")
                
                start_version_file_id = check_node[0][0]["version_file_id"]
            
            framework_files = await self.db_service.call_procedure(
                StoredProcedures.FILTER_VERSION_FILES,
                [start_version_file_id, version_id, None, True, 1, 100000]
            )
            if not framework_files or not framework_files[0]:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch framework version files.")
            
            file_ids = [(file.get("id"),file.get("file_id")) for file in framework_files[0]]
            
            for version_file_id, file_id in file_ids:
                file_details = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_FILES,
                    [file_id, None, None, None, True, 1, 1]
                )
                if not file_details or not file_details[0]:
                    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to fetch file details for file_id={file_id}.")

                file_name = file_details[0][0]["name"]

                # Build the render set from permanent nodes with temp-node overrides.
                # Temp nodes can be partial (only changed nodes), so temp-only rendering
                # may produce no roots and an empty tree.
                base_nodes = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_NODES,
                    [None, version_file_id, None, None, None, None, None, True, 1, 10000000]
                )
                temp_nodes = await self.db_service.call_procedure(
                    StoredProcedures.FILTER_TEMP_NODES,
                    [None, version_file_id, None, None, None, None, None, True, 1, 10000000]
                )

                merged_nodes = {}
                if base_nodes and base_nodes[0]:
                    for node in base_nodes[0]:
                        merged_nodes[node.get("node_id")] = node

                if temp_nodes and temp_nodes[0]:
                    for node in temp_nodes[0]:
                        merged_nodes[node.get("node_id")] = node

                node_details = [list(merged_nodes.values())]

                temp_response = []

                if node_details and node_details[0]:
                    root_ids = []
                    node_mapping = {}
                    for node in node_details[0]:
                        node["children"]=[]
                        node_mapping[node.get("node_id")]=node
                        if not node.get("parent_node_id"):
                            root_ids.append(node.get("node_id"))

                    if not root_ids:
                        # Fallback for partial/mismatched datasets: treat orphan nodes as roots.
                        for node in node_details[0]:
                            parent_node_id = node.get("parent_node_id")
                            if parent_node_id and parent_node_id not in node_mapping:
                                root_ids.append(node.get("node_id"))

                    if start_node_id:
                        root_ids = [start_node_id]
                    
                    for node in node_details[0]:
                        parent_node_id = node.get("parent_node_id")
                        if parent_node_id and parent_node_id in node_mapping:
                            node_mapping[parent_node_id]["children"].append(node.get("node_id"))

                    for root_id in root_ids:
                        temp_response.append(await self._create_tree(root_id, node_mapping, label_config, level))

                response.append(FrameworkTreeResponse(
                    framework_name = framework_name,
                    file_id = file_id,
                    file_name = file_name,
                    tree = temp_response
                ))

            return response
        
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error retrieving tree for framework: {e}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An error occurred: {e}")
