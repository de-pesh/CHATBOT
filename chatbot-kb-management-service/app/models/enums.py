from enum import StrEnum


class ValidExtension(StrEnum):
    '''
    Enum representing valid file extensions.

    Attributes:
        PDF (str): Represents '.pdf' extension.
        DOC (str): Represents '.doc' extension.
        DOCX (str): Represents '.docx' extension.
    '''
    PDF = ".pdf"
    DOC = ".doc"
    DOCX = ".docx"


    @classmethod
    def is_valid(cls, extension: str) -> bool:
        '''
        Checks if a given file extension is valid.

        Parameters:
            extension (str): The file extension to validate.

        Returns:
            bool: True if the extension is valid, False otherwise.
        '''
        return extension.lower() in cls.__members__.values()



class PolicyActionTypeEnum(StrEnum):
    """
    Enum representing the types of actions that can be performed on a policy in the chatbot knowledge base.

    Values:
        CREATE: Represents the creation of a new policy.
        UPDATE: Represents updating an existing policy.
        DELETE: Represents deleting a policy.
    """
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
