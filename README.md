# RAG Based Chatbot

A chatbot that lets you upload a PDF and ask questions about it. It uses retrieval-augmented generation (RAG) to find the most relevant parts of your document and then uses an LLM to answer your question in natural language.

## How It Works

1. You upload a PDF file
2. The app splits the text into small chunks and stores them as vector embeddings
3. When you ask a question, it finds the most relevant chunks using semantic search
4. Those chunks are sent along with your question to an LLM, which generates an answer

## Tech Stack

- **FastAPI** — web server
- **ChromaDB** — vector database for storing and searching document chunks
- **Sentence Transformers** (`all-MiniLM-L6-v2`) — generates embeddings for semantic search
- **Groq** (`llama-3.1-8b-instant`) — LLM for generating answers
- **PyPDF2** — extracts text from PDFs

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

3. Create a `.env` file with your Groq API key:

```
GROQ_API_KEY=your_api_key_here
```

4. Run the app:

```bash
python app.py
```

5. Open `http://127.0.0.1:5000` in your browser, upload a PDF, and start chatting.

## Project Structure

```
├── app.py              # Main application with RAG pipeline
├── requirements.txt    # Python dependencies
├── .env                # API keys (not committed)
├── templates/
│   ├── upload.html     # PDF upload page
│   └── chat.html       # Chat interface
├── static/
│   └── style.css       # Styling
└── uploads/            # Uploaded PDF files
```
