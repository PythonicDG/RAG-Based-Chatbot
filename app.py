import os
import re
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from groq import Groq
import chromadb
from chromadb.utils import embedding_functions
import PyPDF2
from dotenv import load_dotenv

load_dotenv()

UPLOAD_FOLDER = "uploads"
ALLOWED_EXTENSIONS = {"pdf"}
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", 500))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", 50))
TOP_K = int(os.environ.get("TOP_K", 5))
LLM_MODEL = os.environ.get("LLM_MODEL", "llama-3.1-8b-instant")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", 0.3))
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", 1024))

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
chroma_client = chromadb.Client()

embedding_fn = None
embedding_ready = threading.Event()
indexed_collections: dict[str, chromadb.Collection] = {}


def load_embedding_model():
    global embedding_fn
    try:
        print("Loading embedding model...", flush=True)
        embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=EMBEDDING_MODEL
        )
        embedding_ready.set()
        print("Embedding model loaded successfully.", flush=True)
    except Exception as e:
        print(f"Failed to load embedding model: {e}", flush=True)
        embedding_ready.set()


def get_embedding_fn():
    embedding_ready.wait(timeout=300)
    if embedding_fn is None:
        raise RuntimeError("Embedding model failed to load")
    return embedding_fn


@asynccontextmanager
async def lifespan(app: FastAPI):
    threading.Thread(target=load_embedding_model, daemon=True).start()
    yield



app = FastAPI(title="RAG Chatbot", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


class ChatRequest(BaseModel):
    filename: str
    message: str


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def extract_text_from_pdf(pdf_path: str) -> str:
    text = ""
    with open(pdf_path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk.strip())
        start += chunk_size - overlap
    return chunks


def index_pdf(filename: str, pdf_path: str) -> chromadb.Collection:
    collection_name = re.sub(r"[^a-zA-Z0-9_]", "_", filename)[:63]
    if len(collection_name) < 3:
        collection_name = collection_name + "___"

    try:
        chroma_client.delete_collection(collection_name)
    except Exception:
        pass

    collection = chroma_client.create_collection(
        name=collection_name,
        embedding_function=get_embedding_fn(),
    )

    text = extract_text_from_pdf(pdf_path)
    chunks = chunk_text(text)

    if not chunks:
        return collection

    collection.add(
        documents=chunks,
        ids=[f"chunk_{i}" for i in range(len(chunks))],
        metadatas=[{"source": filename, "chunk_index": i} for i in range(len(chunks))],
    )

    return collection


def retrieve_context(collection: chromadb.Collection, query: str, top_k: int = TOP_K) -> str:
    results = collection.query(
        query_texts=[query],
        n_results=min(top_k, collection.count()),
    )
    if results and results["documents"] and results["documents"][0]:
        return "\n\n---\n\n".join(results["documents"][0])
    return ""


def ask_llm(context: str, question: str) -> str:
    system_prompt = (
        "You are a helpful assistant that answers questions based on the provided document context. "
        "Use ONLY the information from the context below to answer the user's question. "
        "If the answer is not found in the context, say so clearly. "
        "Be concise, accurate, and well-structured in your response. "
        "Use bullet points or numbered lists when appropriate."
    )

    user_prompt = (
        f"### Document Context\n{context}\n\n"
        f"### Question\n{question}\n\n"
        "Please answer the question based on the document context above."
    )

    chat_completion = groq_client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=LLM_TEMPERATURE,
        max_tokens=LLM_MAX_TOKENS,
    )
    return chat_completion.choices[0].message.content



@app.get("/health")
async def health():
    return {
        "status": "ok",
        "embedding_ready": embedding_ready.is_set(),
        "embedding_loaded": embedding_fn is not None,
        "groq_key_set": bool(os.environ.get("GROQ_API_KEY")),
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("upload.html", {"request": request})


@app.post("/upload", response_class=HTMLResponse)
async def upload_pdf(request: Request, file: UploadFile = File(...)):
    try:
        if not file.filename:
            raise HTTPException(status_code=400, detail="No selected file")

        if not allowed_file(file.filename):
            raise HTTPException(status_code=400, detail="Invalid file type. Only PDF files are allowed.")

        filename = re.sub(r"[^\w\-.]", "_", file.filename)
        filepath = os.path.join(UPLOAD_FOLDER, filename)

        content = await file.read()
        with open(filepath, "wb") as f:
            f.write(content)

        collection = index_pdf(filename, filepath)
        indexed_collections[filename] = collection
        chunk_count = collection.count()

        return templates.TemplateResponse(
            "chat.html",
            {"request": request, "filename": filename, "chunk_count": chunk_count},
        )

    except HTTPException:
        raise
    except Exception as e:
        print(f"Upload error: {e}", flush=True)
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to process PDF: {str(e)}")


@app.post("/chat")
async def chat(data: ChatRequest):
    if not data.filename or not data.message.strip():
        raise HTTPException(status_code=400, detail="Please provide both a filename and a message.")

    collection = indexed_collections.get(data.filename)
    if collection is None:
        raise HTTPException(status_code=404, detail="PDF not found. Please upload a PDF first.")

    try:
        context = retrieve_context(collection, data.message.strip())

        if not context:
            return {"response": "I couldn't find any relevant information in the document."}

        answer = ask_llm(context, data.message.strip())
        return {"response": answer}

    except Exception as e:
        print(f"Error in chat: {e}", flush=True)
        raise HTTPException(
            status_code=500,
            detail="Sorry, an error occurred while processing your question. Please try again.",
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=int(os.environ.get("PORT", 5001)), reload=True)