"""
This module defines prompt construction utilities for interacting with the LLM.
It formats prompts for rephrasing user queries based on chat history and for 
constructing final response prompts using retrieved context.

Functions:
    - fetch_new_query_prompt: Prepares a prompt for rephrasing a user query using chat history.
    - fetch_final_prompt: Prepares a prompt for answering based on context and user query.
    - format_translate_prompt: Prepares a prompt to translate the final answer into the
      language of the user's query, if different.
"""
def fetch_new_query_prompt(chat_history, query, assessment_active: bool = False):
    """
    Constructs a prompt to rephrase the user's query using the chat history for context.

    The prompt:
    - Asks the model to rephrase or split the query if necessary.
    - Ensures standalone questions remain unaltered.
    - Requests the model to output a structured JSON list of new questions.
    - Asks for the query's language, so an English query can skip the separate
      translation round trip in `format_translate_prompt` entirely.
    - When an assessment is active, also asks the model to classify the user's
      intent as "assessment" (about the specific assessment's controls, evidence,
      results) or "policy" (general framework/policy knowledge), so the caller can
      route retrieval to the correct collection.

    Args:
        chat_history (list): The previous conversation history.
        query (str): The latest user query.
        assessment_active (bool): Whether the request carries an assessment context.

    Returns:
        list: A list containing a dictionary with the constructed prompt formatted for LLM input.
    """

    chat_history_str = str(chat_history)

    # Reporting the query's language here costs nothing - this call already
    # reads the query and already returns JSON - and lets the caller skip the
    # separate translate round trip entirely when the query is English.
    language_instruction = (
        'Also report the language the latest user query is written in, as its '
        'English name (for example "English", "French", "Arabic").\n'
    )

    if assessment_active:
        intent_instructions = (
            "Also classify the latest user query's intent as exactly one of:\n"
            "    - \"assessment\": the user is asking about the specific assessment they are "
            "working on (its controls, mapped evidence, compliance results, scores, status, "
            "recommendations, or summary).\n"
            "    - \"policy\": the user is asking about general framework, policy, regulatory, "
            "or best-practice knowledge that is not tied to their specific assessment's data.\n"
        )
        json_shape = """```json
    {
        "new_questions": [
            "First rephrased or standalone question.",
            "Second rephrased or standalone question (if applicable)."
        ],
        "intent": "assessment",
        "language": "English"
    }
    ```"""
    else:
        intent_instructions = ""
        json_shape = """```json
    {
        "new_questions": [
            "First rephrased or standalone question.",
            "Second rephrased or standalone question (if applicable)."
        ],
        "language": "English"
    }
    ```"""

    query_prompt = f"""Rephrase the user's query using chat history only if needed for context.
    Do not answer the question.
    If the query has multiple questions, split them.
    **Do not rephrase a standalone question with no relevant history.**
    {intent_instructions}{language_instruction}
    Output in JSON like:
    {json_shape}

    <chatHistory>
    {chat_history_str}
    </chatHistory>

    User Query:
    {query}
    """
    return [{"role": "user", "content": query_prompt}]


def fetch_final_prompt(all_results, question, assessment_mode: bool = False):
    """
    Constructs the final response prompt using retrieved context and the user query.

    The prompt:
    - Embeds the retrieved context.
    - Instructs the model on how to handle uncertain knowledge and citations.
    - Imposes strict response behavior when the query is unrelated to context.

    Args:
        all_results (list): List of retrieved context documents or excerpts.
        question (str): The user query to be answered.
        assessment_mode (bool): When True, the context describes a specific
            compliance assessment (overview, controls, mapped evidence, run
            results) rather than general policies, and the prompt is framed
            accordingly.

    Returns:
        list: A list containing a dictionary with the constructed prompt formatted for LLM input.
    """

    if assessment_mode:
        intro_line = (
            "You are a compliance assessment assistant. Use the following assessment "
            "information (overview, controls, mapped evidence, and run results) as your "
            "learned knowledge, inside <context></context> XML tags."
        )
        off_topic_line = (
            '- If the query is not related to the assessment context, politely explain '
            'that you can only answer questions about this assessment, its controls, '
            'evidence, and results.'
        )
    else:
        intro_line = (
            "Use the following context as your learned knowledge, inside <context></context> XML tags."
        )
        off_topic_line = (
            '- If the query is not related to context, strictly respond with "I am sorry I '
            'can not respond. Please ask questions related to the topics in my knowledge base".'
        )

    response_prompt = f"""
    {intro_line}
    <context>
        {all_results}
    </context>

    When answering to user:
    - If you don't know, just say that you don't know.
    - If you don't know when you are not sure, ask for clarification.
    - Strictly do not send emojis in the response.
    Avoid mentioning that you obtained the information from the context.
    Avoid changing the wording of the context provided.
    - "At the end of each response, cite the document names and page numbers **only if** the provided context was used to generate the relevant answer."
    - **Do not mention or refer to the "context" explicitly in your response.**
    - Respond to user greetings with an approprate greeting.
    {off_topic_line}

    Given the context information, answer the query.
    Query: {question}"""

    return [{"role": "user", "content": response_prompt}]


def format_translate_prompt(query_text: str, answer_text: str):
    """
    Constructs a prompt asking the model to detect the language of the user's
    query and, if it is not English, translate the answer into that language.

    Args:
        query_text (str): The original user query, used to detect the target language.
        answer_text (str): The generated answer to translate if needed.

    Returns:
        list: A list containing a dictionary with the constructed prompt formatted for LLM input.
    """

    translate_prompt = f"""Detect the language of the user query below.
    If it is English, return the answer unchanged.
    If it is not English, translate the answer into that same language, preserving
    its meaning, formatting, and any citations exactly.

    Output strictly in JSON like:
    ```json
    {{
        "final_answer": "The answer, translated if needed."
    }}
    ```

    User Query:
    {query_text}

    Answer:
    {answer_text}
    """
    return [{"role": "user", "content": translate_prompt}]
