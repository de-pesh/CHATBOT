import asyncio
from app.rpc_server import serve

if __name__ == "__main__":
    asyncio.run(serve())