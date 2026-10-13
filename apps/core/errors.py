class ApiError(Exception):
    """Ожидаемая ошибка предметной области. Текст пригоден для показа пользователю, `code` — для фронтенда."""

    code = "error"
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message
