from ..config import settings


class RegistrationQueue:
    def __init__(self):
        self._semaphore_holder = None
        self._limit = settings.concurrency_limit

    async def start(self):
        import asyncio

        self._semaphore_holder = asyncio.Semaphore(self._limit)

    @property
    def semaphore(self):
        return self._semaphore_holder

    async def submit(self, task_id: int):
        raise NotImplementedError("注册执行器待实现(步骤 3/4)")
