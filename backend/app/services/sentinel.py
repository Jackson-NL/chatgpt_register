class SentinelClient:
    async def get_token(self, flow: str) -> dict:
        raise NotImplementedError("sentinel token 获取待实现(浏览器内)")

    async def get_so_token(self, flow: str) -> dict:
        raise NotImplementedError("sentinel so-token 获取待实现(浏览器内)")
