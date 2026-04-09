import os
import re
import uuid
import time
import logging
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

APP_VERSION = "1.4.0"
APP_START_TIME = time.time()

UPLOAD_FOLDER = "uploads"
ALLOWED_EXTENSIONS = {"pdf"}
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", 500))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", 50))
TOP_K = int(os.environ.get("TOP_K", 5))
LLM_MODEL = os.environ.get("LLM_MODEL", "llama-3.1-8b-instant")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", 0.3))
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", 1024))

# ── Supported Languages ────────────────────────────────────────────────────────
# Maps language codes to their display names and LLM instruction labels.
# The key is sent by the widget, the value is used in the system prompt.
SUPPORTED_LANGUAGES = {
    "en": {"name": "English",    "instruction": "English"},
    "hi": {"name": "Hindi",      "instruction": "Hindi (हिन्दी)"},
    "mr": {"name": "Marathi",    "instruction": "Marathi (मराठी)"},
    "ta": {"name": "Tamil",      "instruction": "Tamil (தமிழ்)"},
    "te": {"name": "Telugu",     "instruction": "Telugu (తెలుగు)"},
    "bn": {"name": "Bengali",    "instruction": "Bengali (বাংলা)"},
    "gu": {"name": "Gujarati",   "instruction": "Gujarati (ગુજરાતી)"},
    "kn": {"name": "Kannada",    "instruction": "Kannada (ಕನ್ನಡ)"},
    "ml": {"name": "Malayalam",  "instruction": "Malayalam (മലയാളം)"},
    "pa": {"name": "Punjabi",    "instruction": "Punjabi (ਪੰਜਾਬੀ)"},
    "es": {"name": "Spanish",    "instruction": "Spanish (Español)"},
    "fr": {"name": "French",     "instruction": "French (Français)"},
    "de": {"name": "German",     "instruction": "German (Deutsch)"},
    "zh": {"name": "Chinese",    "instruction": "Chinese (中文)"},
    "ja": {"name": "Japanese",   "instruction": "Japanese (日本語)"},
    "ar": {"name": "Arabic",     "instruction": "Arabic (العربية)"},
    "pt": {"name": "Portuguese", "instruction": "Portuguese (Português)"},
}
DEFAULT_LANGUAGE = "en"

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

groq_api_key = os.environ.get("GROQ_API_KEY")
if not groq_api_key:
    logger.warning("GROQ_API_KEY not found in environment variables. LLM features will not work.")

from chromadb.config import Settings

groq_client = Groq(api_key=groq_api_key)
chroma_client = chromadb.PersistentClient(
    path="./chroma_db",
    settings=Settings(anonymized_telemetry=False)
)

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



"""
Main application entry point for the RAG-Based Chatbot SaaS platform.
This module initializes the FastAPI application, mounts static files, 
sets up routes, and manages the lifespan of the application including 
database and embedding model loading.
"""
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

class WidgetChatRequest(BaseModel):
    message: str
    bot_id: int
    session_id: str = "default_session"
    language: str = DEFAULT_LANGUAGE


# Helper to check if the uploaded file has a permitted extension
def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def extract_text_from_pdf(pdf_path: str) -> str:
    text = ""
    try:
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
    except Exception as e:
        logger.error(f"Error extracting text from PDF {pdf_path}: {e}")
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
    """
    Extracts text from a PDF, chunks it, and adds it to the ChromaDB collection for a specific bot.
    
    Args:
        bot_id (int): The ID of the bot.
        doc_id (int): The ID of the document record in the database.
        filename (str): The original filename of the PDF.
        pdf_path (str): The local path to the uploaded PDF file.
        
    Returns:
        chromadb.Collection: The updated ChromaDB collection.
    """
    collection_name = f"bot_{bot_id}"
    
    collection = get_safe_collection(bot_id)

    text = extract_text_from_pdf(pdf_path)
    chunks = chunk_text(text)

    if not chunks:
        logger.warning(f"No text extracted or chunks created for bot {bot_id}, document {doc_id} ({filename})")
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
    """
    Retrieves the most relevant document chunks from the ChromaDB collection for a given query.
    
    Args:
        collection (chromadb.Collection): The ChromaDB collection to query.
        query (str): The user's message/query.
        top_k (int): Number of chunks to retrieve.
        
    Returns:
        str: Concatenated text of the most relevant chunks.
    """
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


def _get_language_instruction(lang_code: str) -> str:
    """Resolve a language code to its LLM instruction label, with safe fallback."""
    lang_code = (lang_code or DEFAULT_LANGUAGE).strip().lower()
    lang_entry = SUPPORTED_LANGUAGES.get(lang_code)
    if lang_entry:
        return lang_entry["instruction"]
    # Fallback: if someone sends a full name like "Hindi", attempt reverse lookup
    for code, entry in SUPPORTED_LANGUAGES.items():
        if entry["name"].lower() == lang_code:
            return entry["instruction"]
    return SUPPORTED_LANGUAGES[DEFAULT_LANGUAGE]["instruction"]


def ask_llm(context: str, question: str, language: str = DEFAULT_LANGUAGE) -> str:
    """
    Generates a response from the LLM based on the provided context and question.
    
    Args:
        context (str): The retrieved document context.
        question (str): The user's question.
        language (str): The target language for the response.
        
    Returns:
        str: The LLM-generated response.
    """
    lang_label = _get_language_instruction(language)
    is_english = language.strip().lower() in ("en", "english")

    # Build a language-aware system prompt
    if is_english:
        system_prompt = (
            "You are a professional assistant that provides clear, concise, and business-appropriate answers based on the provided document context. "
            "Use ONLY the information from the context to answer. "
            "All responses MUST be in English and use professional, formal language. "
            "Ignore the user's tone or slang; do not match it. Use bullet points or numbered lists where appropriate for structured clarity."
        )
    else:
        system_prompt = (
            f"You are a professional assistant that provides clear, concise, and business-appropriate answers based on the provided document context. "
            f"Use ONLY the information from the context to answer. "
            f"CRITICAL INSTRUCTION: You MUST write your ENTIRE response in {lang_label}. "
            f"Do NOT respond in English or any other language — use {lang_label} only. "
            f"Even if the document context is in English, translate and present your answer in {lang_label}. "
            f"Use professional and formal language appropriate for {lang_label}. "
            f"Use bullet points or numbered lists where appropriate for structured clarity."
        )

    user_prompt = (
        f"### Document Context\n{context}\n\n"
        f"### Question\n{question}\n\n"
        f"Please answer the question based on the document context above. "
        f"Remember: respond ONLY in {lang_label}."
    )

    try:
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
    except Exception as e:
        logger.error(f"Groq API error: {e}")
        # Return error message in the requested language if possible
        error_messages = {
            "en": "I'm sorry, I encountered an error while processing your request. Please try again later.",
            "hi": "क्षमा करें, आपके अनुरोध को संसाधित करने में एक त्रुटि हुई। कृपया बाद में पुनः प्रयास करें।",
            "mr": "क्षमस्व, तुमची विनंती प्रक्रिया करताना त्रुटी आली. कृपया नंतर पुन्हा प्रयत्न करा.",
            "es": "Lo siento, encontré un error al procesar su solicitud. Por favor, inténtelo de nuevo más tarde.",
            "fr": "Désolé, une erreur s'est produite lors du traitement de votre demande. Veuillez réessayer plus tard.",
            "de": "Es tut mir leid, bei der Verarbeitung Ihrer Anfrage ist ein Fehler aufgetreten. Bitte versuchen Sie es später erneut.",
        }
        return error_messages.get(language.strip().lower(), error_messages["en"])



@app.get("/health")
async def health():
    uptime_seconds = round(time.time() - APP_START_TIME, 2)
    return {
        "status": "ok",
        "version": APP_VERSION,
        "uptime_seconds": uptime_seconds,
        "embedding_ready": embedding_ready.is_set(),
        "embedding_loaded": embedding_fn is not None,
        "groq_key_set": bool(os.environ.get("GROQ_API_KEY")),
    }


@app.get("/health/detailed")
async def health_detailed():
    """Detailed health check with database and collection stats."""
    uptime_seconds = round(time.time() - APP_START_TIME, 2)
    db = SessionLocal()
    try:
        bot_count = db.query(Bot).count()
        doc_count = db.query(Document).count()
        chat_count = db.query(ChatLog).count()
    finally:
        db.close()

    collections = []
    try:
        for col in chroma_client.list_collections():
            name = col.name if hasattr(col, "name") else str(col)
            collections.append(name)
    except Exception:
        pass

    return {
        "status": "ok",
        "version": APP_VERSION,
        "uptime_seconds": uptime_seconds,
        "embedding_ready": embedding_ready.is_set(),
        "embedding_loaded": embedding_fn is not None,
        "database": {
            "bots": bot_count,
            "documents": doc_count,
            "chat_logs": chat_count,
        },
        "collections": collections,
        "config": {
            "llm_model": LLM_MODEL,
            "embedding_model": EMBEDDING_MODEL,
            "chunk_size": CHUNK_SIZE,
            "top_k": TOP_K,
        },
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

        return templates.TemplateResponse(
            request=request,
            name="dashboard/home.html",
            context={
                "user": user,
                "bots": bots,
                "total_docs": total_docs,
                "total_chats": total_chats,
            }
        )
    finally:
        db.close()


@app.get("/dashboard/bots/new", response_class=HTMLResponse)
async def create_bot_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/auth/login", status_code=303)
    return templates.TemplateResponse(request=request, name="dashboard/create_bot.html", context={"user": user})


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
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating bot: {e}")
        raise HTTPException(status_code=500, detail="Failed to create bot")
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
        
        return templates.TemplateResponse(
            request=request,
            name="dashboard/bot_details.html",
            context={"bot": bot}
        )
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
        
        server_url = f"{request.url.scheme}://{request.url.netloc}"
        
        return templates.TemplateResponse(
            request=request,
            name="dashboard/embed.html",
            context={
                "bot": bot,
                "server_url": server_url
            }
        )
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
        
        logs = db.query(ChatLog).filter(ChatLog.bot_id == bot_id).order_by(ChatLog.created_at.desc()).limit(100).all()
        
        total_messages = db.query(ChatLog).filter(ChatLog.bot_id == bot_id).count()
        unique_sessions = db.query(ChatLog.session_id).filter(ChatLog.bot_id == bot_id).distinct().count()
        
        avg_response_time = 0
        if total_messages > 0:
            from sqlalchemy import func
            avg_response_time = db.query(func.avg(ChatLog.response_time_ms)).filter(ChatLog.bot_id == bot_id).scalar() or 0
            
        return templates.TemplateResponse(
            request=request,
            name="dashboard/analytics.html",
            context={
                "bot": bot,
                "logs": logs,
                "total_messages": total_messages,
                "unique_sessions": unique_sessions,
                "avg_response_time": avg_response_time
            }
        )
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

        new_doc = Document(
            bot_id=bot.id,
            filename=filename,
            original_name=file.filename,
            chunk_count=0
        )
        db.add(new_doc)
        db.commit()
        db.refresh(new_doc)

        try:
            collection = index_pdf(bot.id, new_doc.id, file.filename, filepath)
        except Exception as index_err:
            logger.error(f"Indexing error: {index_err}")
            # We keep the document record but it might not be indexed properly
        
        return RedirectResponse(f"/dashboard/bots/{bot_id}", status_code=303)

    except Exception as e:
        db.rollback()
        logger.error(f"Upload error: {e}")
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

        try:
            collection = get_safe_collection(bot_id)
            collection.delete(where={"doc_id": doc_id})
        except Exception as chroma_err:
            logger.error(f"ChromaDB deletion error: {chroma_err}")

        filepath = os.path.join(UPLOAD_FOLDER, doc.filename)
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception as os_err:
                logger.error(f"OS file removal error: {os_err}")

        db.delete(doc)
        db.commit()

        return RedirectResponse(f"/dashboard/bots/{bot_id}", status_code=303)
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting document: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete document")
    finally:
        db.close()


@app.get("/api/widget/documents")
async def widget_list_documents():
    """List all uploaded PDFs available for the widget."""
    return {
        "documents": []
    }


@app.get("/api/widget/languages")
async def widget_languages():
    """Returns all supported languages for the widget language selector."""
    return {
        "languages": [
            {"code": code, "name": entry["name"]}
            for code, entry in SUPPORTED_LANGUAGES.items()
        ],
        "default": DEFAULT_LANGUAGE,
    }


@app.post("/api/widget/chat")
async def widget_chat(data: WidgetChatRequest):
    """Widget chat endpoint — external websites call this."""
    if not data.bot_id or not data.message.strip():
        raise HTTPException(status_code=400, detail="Please provide both a bot_id and a message.")

    # Validate and normalize language with safe fallback
    language = (data.language or DEFAULT_LANGUAGE).strip().lower()
    if language not in SUPPORTED_LANGUAGES:
        logger.warning(f"Unsupported language code '{data.language}' received, falling back to '{DEFAULT_LANGUAGE}'.")
        language = DEFAULT_LANGUAGE

    try:
        collection = get_safe_collection(data.bot_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Bot not found or has no documents.")

    start_time = time.time()
    try:
        context = retrieve_context(collection, data.message.strip())

        if not context:
            no_info_messages = {
                "en": "I couldn't find any relevant information in the document.",
                "hi": "मुझे दस्तावेज़ में कोई प्रासंगिक जानकारी नहीं मिली।",
                "mr": "मला दस्तऐवजात कोणतीही संबंधित माहिती सापडली नाही.",
                "es": "No pude encontrar información relevante en el documento.",
                "fr": "Je n'ai trouvé aucune information pertinente dans le document.",
                "de": "Ich konnte keine relevanten Informationen im Dokument finden.",
            }
            answer = no_info_messages.get(language, no_info_messages["en"])
        else:
            answer = ask_llm(context, data.message.strip(), language=language)
        
        db = SessionLocal()
        try:
            log = ChatLog(
                bot_id=data.bot_id,
                session_id=data.session_id,
                user_message=data.message.strip(),
                bot_response=answer,
                response_time_ms=(time.time() - start_time) * 1000,
                language=language,
            )
            db.add(log)
            db.commit()
        finally:
            db.close()

        return {"response": answer, "language": language}

    except Exception as e:
        import traceback
        logger.error(f"Widget chat error: {e}", exc_info=True)
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Server error: {str(e)}",
        )


if __name__ == "__main__":
    import uvicorn
    # Use 0.0.0.0 so the app is accessible outside the container
    # Use Railway's/Heroku's PORT env variable, or default to 5001
    # Use 0.0.0.0 for Railway/containerized environments to be accessible
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)