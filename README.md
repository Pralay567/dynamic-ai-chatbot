<<<<<<< HEAD
# Dynamic AI Chatbot

A modern, web-based conversational AI application built with **Python, Flask, and JavaScript**. The chatbot supports contextual conversations, multiple languages, conversation history, analytics, and customizable appearance.

The application uses a configurable AI provider, with **OpenRouter** as the default configuration and Google Gemini available as an alternative.

## Features

- **AI-powered conversations:** Generate responses using a configured AI provider.
- **Conversation context:** Include recent messages from previous turns to support contextual responses.
- **Multilingual interaction:** Supports English, Hindi, and Hinglish.
- **Conversation management:** Store and manage conversations and messages using SQLite.
- **Analytics dashboard:** View conversation-related analytics provided by the application.
- **Customizable interface:** Switch between light, dark, and automatic themes.
- **Chat settings:** Configure available preferences, including creativity and context-related settings.
- **Chat history tools:** Export or clear chat history through the interface.
- **API protection:** Configurable request limits and message-length restrictions.
- **Configuration through environment variables:** Keep API credentials separate from application source code.
- **Automated tests:** Test configuration, provider handling, API endpoints, database behavior, rate limiting, and security-related functionality.

## Technology Stack

| Component | Technology |
|---|---|
| Backend | Python, Flask |
| Frontend | HTML, CSS, JavaScript |
| Database | SQLite |
| AI provider | OpenRouter or Google Gemini |
| Testing | Python `unittest` |
| Configuration | Environment variables and `.env` |
| Deployment entry point | `wsgi.py` |

## Project Structure

```text
Ai chatbot/
├── run.py
├── server.py
├── wsgi.py
├── config.py
├── ai_provider.py
├── chat_service.py
├── db.py
├── errors.py
├── logging_setup.py
├── prompts.py
├── ratelimit.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── test_api.py
├── tests/
│   ├── helpers.py
│   ├── test_ai_provider.py
│   ├── test_chat_endpoint.py
│   ├── test_config.py
│   ├── test_conversations_api.py
│   ├── test_db.py
│   ├── test_logging_redaction.py
│   ├── test_ratelimit.py
│   ├── test_secrets_and_repo_hygiene.py
│   └── test_server_module.py
└── static/
    ├── index.html
    ├── style.css
    └── app.js
```

## Requirements

- Python 3.10 or a compatible version supported by the project's dependencies.
- pip
- An API key for your chosen AI provider.
- An internet connection for requests to the external AI API.

The project was developed and tested in a Python 3.13 environment. Dependency compatibility may vary by operating system and Python version.

## Installation and Setup

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
cd YOUR_REPOSITORY
```

Replace the example repository address with your actual GitHub repository URL.

### 2. Create a virtual environment

**Windows PowerShell:**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, consult the Python documentation or use a terminal configuration appropriate for your system.

### 3. Install dependencies

```powershell
python -m pip install -r requirements.txt
```

### 4. Create your local environment file

Copy the example configuration:

```powershell
Copy-Item .env.example .env
```

Open `.env` in your editor and configure your selected AI provider.

### 5. Configure OpenRouter

OpenRouter is the default provider in the example configuration.

Create an API key through [OpenRouter](https://openrouter.ai/keys), then configure your local `.env` file:

```dotenv
AI_PROVIDER=openrouter
OPENROUTER_API_KEY=YOUR_OPENROUTER_API_KEY
OPENROUTER_MODEL=openrouter/free
```

Replace `YOUR_OPENROUTER_API_KEY` with your actual key **only in your local `.env` file**.

The `openrouter/free` model identifier uses OpenRouter's free-model routing. Availability, rate limits, and model selection depend on OpenRouter's current offerings.

Check [OpenRouter's model directory](https://openrouter.ai/models) for supported models.

### 6. Alternatively, configure Google Gemini

If you want to use Gemini instead, create an API key through [Google AI Studio](https://aistudio.google.com/apikey) and configure:

```dotenv
AI_PROVIDER=gemini
GEMINI_API_KEY=YOUR_GEMINI_API_KEY
GEMINI_MODEL=YOUR_SUPPORTED_GEMINI_MODEL
```

Replace the placeholders with your own credentials and a currently supported Gemini model identifier.

Model availability and API quotas can change. Check the [Gemini API documentation](https://ai.google.dev/gemini-api/docs) for current model information.

**Important:** Configure only the provider you intend to use. Never commit your actual `.env` file or place real API keys in the example configuration.

## Running the Application

With the virtual environment activated and dependencies installed, run:

```powershell
python run.py
```

The default local configuration uses:

```dotenv
HOST=127.0.0.1
PORT=5000
APP_DEBUG=false
```

Open the application in your browser:

http://127.0.0.1:5000

Keep the terminal open while using the application. Stop the development server with `Ctrl+C`.

For deployment, configure the host, port, environment variables, and WSGI server according to the hosting platform. Do not expose Flask's debug mode on a public deployment.

## Configuration Reference

The `.env.example` file documents the application's supported settings. Important configuration groups include:

| Category | Settings |
|---|---|
| AI provider | `AI_PROVIDER`, `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` |
| Gemini alternative | `GEMINI_API_KEY`, `GEMINI_MODEL` |
| Generation | `DEFAULT_TEMPERATURE`, `GEMINI_MAX_OUTPUT_TOKENS` |
| Conversation history | `HISTORY_MAX_MESSAGES`, `HISTORY_MAX_CHARS`, `MAX_CONVERSATION_MESSAGES` |
| Database | `DATABASE_PATH` |
| Server | `HOST`, `PORT`, `APP_DEBUG` |
| Request protection | `MAX_MESSAGE_CHARS`, `RATE_LIMIT_PER_MINUTE`, `API_RATE_LIMIT_PER_MINUTE` |
| Browser access | `CORS_ALLOWED_ORIGINS`, `TRUSTED_PROXY_COUNT` |
| Logging | `LOG_LEVEL`, `LOG_FILE` |

Additional settings and their defaults are documented in `.env.example`.

## Running Tests

Run the automated test suite from the project root:

```powershell
python -m unittest discover -s tests -v
```

The test suite covers application configuration, AI provider handling, chat endpoints, conversation APIs, database operations, logging redaction, rate limiting, and repository security checks.

The automated tests should be run before making significant changes or publishing a new version.

### Live API testing

The `test_api.py` file is intended for API-related testing. Review the file before running it to determine whether it makes real external API requests. Live tests may consume provider quota and require valid credentials.

## Security and Privacy

- **Never commit `.env`.** It contains private credentials and local configuration.
- Keep `.env.example` free of real keys and passwords.
- Never embed private API keys in frontend JavaScript, HTML, CSS, or other publicly served files.
- Rotate any API key that has accidentally been exposed or committed.
- Keep Flask debug mode disabled on public deployments.
- Configure CORS and trusted proxy settings appropriately for your hosting environment.
- Avoid sharing database files or logs that may contain sensitive application data.
- Chat messages sent to an external AI provider are subject to that provider's processing and privacy policies.
- Conversation data stored locally is subject to the application's database and retention behavior.

The `.gitignore` file excludes local environment configuration and other development artifacts. Always inspect staged files before committing or pushing.

## Limitations

- AI responses depend on the selected provider, model availability, and API quotas.
- Free models may have limited capacity, changing availability, or request restrictions.
- The application requires network access to communicate with the configured external AI provider.
- Response quality and latency can vary by model and provider.
- Deployment requirements may differ from local development settings.

## Future Improvements

Potential future enhancements include:

- Additional AI provider integrations.
- Improved conversation search and organization.
- More detailed analytics and visualizations.
- Enhanced deployment and monitoring options.
- Additional automated integration tests.

## License

No license has been specified in this repository yet. Add a `LICENSE` file if you intend to publish the project under a particular open-source license.

---

**Dynamic AI Chatbot** — a configurable conversational AI application built with Python and web technologies.
=======
# dynamic-ai-chatbot
An interactive AI chatbot built with Python, Flask, and Google Gemini API, featuring real-time Indian date and time awareness, multilingual responses, and a responsive chat interface.
>>>>>>> origin/main
