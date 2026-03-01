import asyncio

from zai_chat_client import ZaiClient


async def main() -> None:
    client = ZaiClient(
        session="main",
        cookies_path="cookies.txt",
        headless=False,
        enable_logs=True,
    )
    await client.start()
    try:
        chat = await client.create_chat(model="GLM-5", deep_think=False, web_search=False)
        msg = await chat.send_message("Hi! Reply in one short sentence.")
        print(msg.response_text)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())

