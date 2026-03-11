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
        # 1) Create chat with explicit initial options.
        chat = await client.create_chat(
            model="GLM-4.7",
            deep_think=False,
            web_search=False,
        )

        # 2) Plain message.
        plain = await chat.send_message(
            "Write one short sentence about test automation."
        )
        print("PLAIN:", plain.response_text)

        # 3) Toggle modes and send again.
        await chat.set_deep_think(True)
        await chat.set_web_search(True)
        advanced = await chat.send_message(
            "Give three concise tips for improving flaky UI tests."
        )
        print("ADVANCED:", advanced.response_text)

        # 4) Read chat history from memory.
        print("\nHistory snapshot:")
        for i, entry in enumerate(chat.messages, 1):
            text_preview = entry.text.replace("\n", " ")[:120]
            print(
                f"{i:02d}. role={entry.role:<9} "
                f"chars={entry.response_chars:<5} "
                f"regen={entry.can_regenerate} "
                f"text={text_preview}"
            )

        # 5) Optional explicit refresh from DOM.
        await chat.refresh_messages()
        print(f"\nHistory size after refresh: {len(chat.messages)}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
