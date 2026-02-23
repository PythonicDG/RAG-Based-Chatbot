# RAG Chatbot: SaaS Platform 🤖

A production-ready chatbot platform that lets you manage multiple chatbots, upload document collections, and embed them on any website. It uses Retrieval-Augmented Generation (RAG) with ChromaDB and Groq LLMs.

## Key Features

- **Admin Dashboard**: Manage multiple bots, document collections, and view analytics.
- **Secure Auth**: User signup/login system with persistent sessions.
- **Bot-Centric RAG**: Chatbots only answer based on documents assigned to them.
- **Embeddable Widget**: A professionally styled, single-line script to add chat to any site.
- **Analytics**: Track message counts, unique sessions, and response latency.

## Setup

1. Clone the repo and create a virtual environment:

```bash
python -m venv venv
venv\Scripts\activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Create a `.env` file with your keys:

```
GROQ_API_KEY=your_groq_key
SECRET_KEY=your_session_secret
```

4. Run the app:

```bash
python app.py
```

5. Visit `http://127.0.0.1:5001/auth/signup` to create your admin account.

## Project Structure

```
├── app.py              # Backend API & Dashboard Routes
├── auth.py             # Authentication Logic
├── models.py           # SQLAlchemy Database Models
├── static/
│   └── widget.js       # Embeddable Chat Widget
├── templates/
│   ├── auth/           # Login/Signup Pages
│   └── dashboard/      # Admin Panel & Stats
├── instance/           # SQLite Database Files
└── chroma_db/          # Vector Store Index
```
