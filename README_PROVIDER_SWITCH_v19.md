QUANTS PRO V16.1 — Provider Switch v19
Patch contains only quants/ai_engine.py.

The sidebar selector already supports Gemini/OpenAI. This patch makes both provider credentials and model names read from Streamlit Secrets as well as environment variables:
- GEMINI_API_KEY
- GEMINI_MODEL (only allowlisted model IDs are accepted)
- OPENAI_API_KEY
- OPENAI_MODEL (defaults to gpt-4o-mini if not specified)

In Streamlit Cloud: App -> Settings -> Secrets, add both providers if you want to switch between them:
GEMINI_API_KEY = "your Gemini key"
GEMINI_MODEL = "gemini-3.6-flash"
OPENAI_API_KEY = "your OpenAI API key"
OPENAI_MODEL = "gpt-4o-mini"

Never commit real API keys into GitHub or paste them in chat. The dropdown switches which provider is used when running AI. OpenAI API billing is separate from ChatGPT subscription.
The Gemini quota guard remains: on HTTP 429 it stops fallback calls and reports quota exhaustion.

Deployment: replace only repository path quants/ai_engine.py, commit, wait for Streamlit Cloud redeploy. Select provider in sidebar, then run AI. This patch does not fix the separate whole-market GTGD/foreign-net data source.
