import os
import re
import uuid
import time
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, UploadFile, File, HTTPException, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from groq import Groq
import chromadb
from chromadb.utils import embedding_functions
import PyPDF2
from dotenv import load_dotenv
from models import init_db, SessionLocal, Bot, Document, ChatLog
from auth import router as auth_router, get_current_user

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
chroma_client = chromadb.PersistentClient(path="./chroma_db")

embedding_fn = None
embedding_ready = threading.Event()



def load_embedding_model():
    global embedding_fn
    try:
        print("Loading embedding model (local)...", flush=True)
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


def get_safe_collection(bot_id: int):
    """Helper to get a collection, handling embedding function conflicts."""
    collection_name = f"bot_{bot_id}"
    try:
        # Try to get the existing collection
        return chroma_client.get_collection(
            name=collection_name,
            embedding_function=get_embedding_fn()
        )
    except Exception as e:
        err_str = str(e)
        if "Embedding function conflict" in err_str or "already exists" in err_str:
            print(f"Conflict detected for {collection_name}. Resetting collection for migration...", flush=True)
            try:
                chroma_client.delete_collection(name=collection_name)
            except Exception as delete_err:
                print(f"Failed to delete collection {collection_name}: {delete_err}", flush=True)
            return chroma_client.create_collection(
                name=collection_name,
                embedding_function=get_embedding_fn()
            )
        elif "does not exist" in err_str or "not found" in err_str.lower():
            return chroma_client.create_collection(
                name=collection_name,
                embedding_function=get_embedding_fn()
            )
        raise e


def reload_collections():
    """Reload existing ChromaDB collections after server restart."""
    embedding_ready.wait(timeout=300)
    if embedding_fn is None:
        return
    for col_name in chroma_client.list_collections():
        try:
            name = col_name.name if hasattr(col_name, 'name') else str(col_name)
            if name.startswith("bot_"):
                bot_id = int(name.replace("bot_", ""))
                get_safe_collection(bot_id)
            print(f"Reloaded collection: {col_name}", flush=True)
        except Exception as e:
            print(f"Failed to reload {col_name}: {e}", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    threading.Thread(target=load_embedding_model, daemon=True).start()
    threading.Thread(target=reload_collections, daemon=True).start()
    yield



app = FastAPI(title="RAG Chatbot", lifespan=lifespan)

app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SECRET_KEY", "super-secret-change-me-in-prod"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

app.include_router(auth_router, prefix="/auth")


# Removed legacy ChatRequest class


class WidgetChatRequest(BaseModel):
    message: str
    bot_id: int
    session_id: str = "default_session"


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


def index_pdf(bot_id: int, doc_id: int, filename: str, pdf_path: str) -> chromadb.Collection:
    collection_name = f"bot_{bot_id}"
    
    # Get or create collection safely
    collection = get_safe_collection(bot_id)

    text = extract_text_from_pdf(pdf_path)
    chunks = chunk_text(text)

    if not chunks:
        return collection

    collection.add(
        documents=chunks,
        ids=[f"bot_{bot_id}_doc_{doc_id}_chunk_{i}" for i in range(len(chunks))],
        metadatas=[{
            "bot_id": bot_id,
            "doc_id": doc_id,
            "source": filename,
            "chunk_index": i
        } for i in range(len(chunks))],
    )

    return collection


def retrieve_context(collection: chromadb.Collection, query: str, top_k: int = TOP_K) -> str:
    count = collection.count()
    if count == 0:
        return ""
        
    results = collection.query(
        query_texts=[query],
        n_results=min(top_k, count),
    )
    if results and results["documents"] and results["documents"][0]:
        return "\n\n---\n\n".join(results["documents"][0])
    return ""


def ask_llm(context: str, question: str) -> str:
    system_prompt = (
        "You are a friendly, casual assistant that answers questions based on the provided document context. "
        "Use ONLY the information from the context to answer. "
        "IMPORTANT: Detect the language the user is writing in and reply in the SAME language. "
        "If the user writes in Hindi, reply in Hindi. If in Marathi, reply in Marathi. "
        "If the user uses slang or casual tone, match their vibe — be chill, not robotic. "
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
    user = get_current_user(request)
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    return RedirectResponse("/auth/login", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/auth/login", status_code=303)

    db = SessionLocal()
    try:
        bots = db.query(Bot).filter(Bot.user_id == user.id).all()
        total_docs = db.query(Document).filter(
            Document.bot_id.in_([b.id for b in bots])
        ).count() if bots else 0
        total_chats = db.query(ChatLog).filter(
            ChatLog.bot_id.in_([b.id for b in bots])
        ).count() if bots else 0

        return templates.TemplateResponse("dashboard/home.html", {
            "request": request,
            "user": user,
            "bots": bots,
            "total_docs": total_docs,
            "total_chats": total_chats,
        })
    finally:
        db.close()


@app.get("/dashboard/bots/new", response_class=HTMLResponse)
async def create_bot_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/auth/login", status_code=303)
    return templates.TemplateResponse("dashboard/create_bot.html", {"request": request, "user": user})


@app.post("/dashboard/bots/new")
async def create_bot(
    request: Request,
    name: str = Form(...),
    welcome_message: str = Form("Hi there! 👋 Ask me anything about the document."),
    primary_color: str = Form("#6C63FF")
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/auth/login", status_code=303)
    
    db = SessionLocal()
    try:
        new_bot = Bot(
            user_id=user.id,
            name=name,
            welcome_message=welcome_message,
            primary_color=primary_color
        )
        db.add(new_bot)
        db.commit()
        return RedirectResponse("/dashboard", status_code=303)
    finally:
        db.close()


@app.get("/dashboard/bots/{bot_id}", response_class=HTMLResponse)
async def bot_details(request: Request, bot_id: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/auth/login", status_code=303)
    
    db = SessionLocal()
    try:
        bot = db.query(Bot).filter(Bot.id == bot_id, Bot.user_id == user.id).first()
        if not bot:
            raise HTTPException(status_code=404, detail="Bot not found")
        
        # For now, just a placeholder or list docs
        return templates.TemplateResponse("dashboard/bot_details.html", {
            "request": request,
            "user": user,
            "bot": bot
        })
    finally:
        db.close()


@app.get("/dashboard/bots/{bot_id}/embed", response_class=HTMLResponse)
async def bot_embed(request: Request, bot_id: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/auth/login", status_code=303)
    
    db = SessionLocal()
    try:
        bot = db.query(Bot).filter(Bot.id == bot_id, Bot.user_id == user.id).first()
        if not bot:
            raise HTTPException(status_code=404, detail="Bot not found")
        
        # Determine current server host
        server_url = f"{request.url.scheme}://{request.url.netloc}"
        
        return templates.TemplateResponse("dashboard/embed.html", {
            "request": request,
            "user": user,
            "bot": bot,
            "server_url": server_url
        })
    finally:
        db.close()


@app.get("/dashboard/bots/{bot_id}/analytics", response_class=HTMLResponse)
async def bot_analytics(request: Request, bot_id: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/auth/login", status_code=303)
    
    db = SessionLocal()
    try:
        bot = db.query(Bot).filter(Bot.id == bot_id, Bot.user_id == user.id).first()
        if not bot:
            raise HTTPException(status_code=404, detail="Bot not found")
        
        # Get logs and aggregate stats
        logs = db.query(ChatLog).filter(ChatLog.bot_id == bot_id).order_by(ChatLog.created_at.desc()).limit(100).all()
        
        total_messages = db.query(ChatLog).filter(ChatLog.bot_id == bot_id).count()
        unique_sessions = db.query(ChatLog.session_id).filter(ChatLog.bot_id == bot_id).distinct().count()
        
        avg_response_time = 0
        if total_messages > 0:
            from sqlalchemy import func
            avg_response_time = db.query(func.avg(ChatLog.response_time_ms)).filter(ChatLog.bot_id == bot_id).scalar() or 0
            
        return templates.TemplateResponse("dashboard/analytics.html", {
            "request": request,
            "user": user,
            "bot": bot,
            "logs": logs,
            "total_messages": total_messages,
            "unique_sessions": unique_sessions,
            "avg_response_time": avg_response_time
        })
    finally:
        db.close()

@app.post("/dashboard/bots/{bot_id}/upload")
async def upload_bot_pdf(request: Request, bot_id: int, file: UploadFile = File(...)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/auth/login", status_code=303)
    
    db = SessionLocal()
    try:
        bot = db.query(Bot).filter(Bot.id == bot_id, Bot.user_id == user.id).first()
        if not bot:
            raise HTTPException(status_code=404, detail="Bot not found")

        if not file.filename:
            raise HTTPException(status_code=400, detail="No selected file")

        if not allowed_file(file.filename):
            raise HTTPException(status_code=400, detail="Invalid file type. Only PDF files are allowed.")

        safe_filename = re.sub(r'[^\w\-.]', '_', file.filename)
        filename = f"{uuid.uuid4().hex}_{safe_filename}"
        filepath = os.path.join(UPLOAD_FOLDER, filename)

        content = await file.read()
        with open(filepath, "wb") as f:
            f.write(content)

        # 1. Create DB record
        new_doc = Document(
            bot_id=bot.id,
            filename=filename,
            original_name=file.filename,
            chunk_count=0
        )
        db.add(new_doc)
        db.commit()
        db.refresh(new_doc)

        # 2. Index in Chroma
        collection = index_pdf(bot.id, new_doc.id, file.filename, filepath)
        
        # 3. Update chunk count
        # In a real app we might count specific chunks but here we just know it succeeded
        # We can re-fetch or count from index_pdf if we want to be precise
        
        return RedirectResponse(f"/dashboard/bots/{bot_id}", status_code=303)

    except Exception as e:
        print(f"Upload error: {e}", flush=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.post("/dashboard/bots/{bot_id}/documents/{doc_id}/delete")
async def delete_document(request: Request, bot_id: int, doc_id: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/auth/login", status_code=303)
    
    db = SessionLocal()
    try:
        doc = db.query(Document).filter(
            Document.id == doc_id,
            Document.bot_id == bot_id
        ).first()
        
        if not doc or doc.bot.user_id != user.id:
            raise HTTPException(status_code=404, detail="Document not found")

        # 1. Delete from Chroma
        collection = get_safe_collection(bot_id)
        collection.delete(where={"doc_id": doc_id})

        # 2. Delete file
        filepath = os.path.join(UPLOAD_FOLDER, doc.filename)
        if os.path.exists(filepath):
            os.remove(filepath)

        # 3. Delete from DB
        db.delete(doc)
        db.commit()

        return RedirectResponse(f"/dashboard/bots/{bot_id}", status_code=303)
    finally:
        db.close()


# ── Widget API (for embeddable chatbot) ──────────────────────────────────────

@app.get("/api/widget/documents")
async def widget_list_documents():
    """List all uploaded PDFs available for the widget."""
    # This endpoint is no longer directly tied to indexed_collections
    # It would need to query the DB for available bots/documents
    # For now, returning an empty list or adapting based on actual bot data
    return {
        "documents": [] # Placeholder, as indexed_collections is removed
    }


@app.post("/api/widget/chat")
async def widget_chat(data: WidgetChatRequest):
    """Widget chat endpoint — external websites call this."""
    if not data.bot_id or not data.message.strip():
        raise HTTPException(status_code=400, detail="Please provide both a bot_id and a message.")

    try:
        collection = get_safe_collection(data.bot_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Bot not found or has no documents.")

    start_time = time.time()
    try:
        context = retrieve_context(collection, data.message.strip())

        if not context:
            answer = "I couldn't find any relevant information in the document."
        else:
            answer = ask_llm(context, data.message.strip())
        
        # Log the chat
        db = SessionLocal()
        try:
            log = ChatLog(
                bot_id=data.bot_id,
                session_id=data.session_id,
                user_message=data.message.strip(),
                bot_response=answer,
                response_time_ms=(time.time() - start_time) * 1000
            )
            db.add(log)
            db.commit()
        finally:
            db.close()

        return {"response": answer}

    except Exception as e:
        import traceback
        print(f"Widget chat error: {e}", flush=True)
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail="Sorry, an error occurred on the server. Please check the logs.",
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=int(os.environ.get("PORT", 5001)), reload=True)