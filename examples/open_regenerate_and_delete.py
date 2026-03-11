import asyncio

from zai_chat_client import ZaiClient


# Put an existing chat URL or id here.
CHAT_REF = ""


async def main() -> None:
    client = ZaiClient(
        session="main",
        cookies_path="cookies.txt",
        headless=False,
        enable_logs=True,
    )
    await client.start()
    try:
        # 1) Open existing chat by id or full URL.
        chat = await client.open_chat(CHAT_REF)
        print(f"Opened chat: id={chat.chat_id}, url={chat.url}")

        # 2) Send message in opened chat.
        msg = await chat.send_message(
            "Reply with a short checklist for debugging failed browser tests."
        )
        print("First response chars:", msg.response_chars)

        # 3) Regenerate the same response.
        regenerated = await msg.regenerate()
        print("Regenerated chars:", regenerated.response_chars)

        # 4) Show last 3 history entries.
        await chat.refresh_messages()
        print("\nLast entries:")
        for entry in chat.messages[-3:]:
            preview = entry.text.replace("\n", " ")[:120]
            print(f"- {entry.role}: {preview}")

        # 5) Optional cleanup (uncomment if you want to delete this chat).
        # deleted = await chat.delete()
        # print("Deleted:", deleted)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
