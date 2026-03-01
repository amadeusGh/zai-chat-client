"""Centralized UI selectors used by the client.

Keeping selectors in one module makes updates safer when the site UI changes.
"""

USER_PROFILE_IMAGE = "img[alt='User profile']"
NEW_CHAT_BUTTON_ID = "#sidebar-new-chat-button"
NEW_CHAT_BUTTON_NAME = "New Chat"
SIGN_IN_BUTTON_NAME = "Sign in"

CHAT_INPUT_TEXTAREA = "textarea#chat-input"
CHAT_INPUT_FALLBACK = "textarea, div[contenteditable='true']"
CHAT_INPUT_CANDIDATES = (
    "textarea#chat-input",
    "textarea",
    "div[contenteditable='true']",
    "[role='textbox'][contenteditable='true']",
)

SEND_MESSAGE_BUTTON = "#send-message-button"
RESPONSE_CONTAINER = ".chat-assistant #response-content-container"

CHAT_MODE_TAB = "button[role='tab'][data-value='chat']"
MODEL_SELECTOR_BUTTON = "button[aria-label='Select a model']"
MODEL_SELECTOR_BUTTON_FALLBACK = "button[id^='model-selector-'][id$='-button']"
MODEL_MENU = "div[role='menu'][data-melt-dropdown-menu]"
MODEL_ITEM_BUTTON = "button[aria-label='model-item']"

DEEP_THINK_BUTTON = "button[data-autothink]"
WEB_SEARCH_SVG_PATH_PREFIX = "svg path[d^='M0.665039 7.33166H13.9984']"

ASSISTANT_WRAPPER = "div[id^='message-']"
REGENERATE_BUTTON = "button.regenerate-response-button"

CHAT_MENU_BUTTON = "button[aria-label='Chat Menu']"
SIDEBAR_TOGGLE_BUTTON = "#sidebar-toggle-button"

THINKING_CONTAINER = ".thinking-chain-container"
THINKING_SHIMMER = "span.shimmer"
GEN_DOT = ".dot"

