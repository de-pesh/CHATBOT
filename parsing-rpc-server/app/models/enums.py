from enum import Enum


class ValidExtension(Enum):
    '''
    Enum representing valid file extensions.

    Attributes:
        PDF (str): Represents '.pdf' extension.
        WORD (tuple): Represents '.docx' and '.doc' extensions.
        IMAGE (tuple): Represents '.png', '.jpeg' and '.jpg' extensions.
        TEXT (str): Represents '.txt' extension.
        EXCEL (str): Represents '.xlsx' extension.
    '''
    PDF = ".pdf"
    WORD = (".docx", ".doc")
    IMAGE = (".png", ".jpeg", ".jpg")
    TEXT = ".txt"
    EXCEL = ".xlsx"


    @classmethod
    def is_valid(cls, extension: str) -> bool:
        '''
        Checks if a given file extension is valid.

        Parameters:
            extension (str): The file extension to validate.

        Returns:
            bool: True if the extension is valid, False otherwise.
        '''
        for value in cls:
            if isinstance(value.value, str):
                if extension == value.value:
                    return True
            elif isinstance(value.value, (list, tuple)):
                if extension in value.value:
                    return True
        return False
        # return extension.lower() in cls.__members__.values()
