import os
import re
from flask import Flask, request, render_template, jsonify
from werkzeug.utils import secure_filename
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

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
chroma_client = chromadb.Client()
embedding_fn = None
indexed_collections: dict[str, chromadb.Collection] = {}


def get_embedding_fn():
    global embedding_fn
    if embedding_fn is None:
        print("Loading embedding model...")
        embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=EMBEDDING_MODEL
        )
        print("Embedding model loaded.")
    return embedding_fn


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


@app.route("/", methods=["GET"])
def index():
    return render_template("upload.html")


@app.route("/upload", methods=["POST"])
def upload_pdf():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(filepath)

        collection = index_pdf(filename, filepath)
        indexed_collections[filename] = collection
        chunk_count = collection.count()

        return render_template("chat.html", filename=filename, chunk_count=chunk_count)

    return jsonify({"error": "Invalid file type. Only PDF files are allowed."}), 400


@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    filename = data.get("filename")
    user_message = data.get("message", "").strip()

    if not filename or not user_message:
        return jsonify({"response": "Please provide both a filename and a message."}), 400

    collection = indexed_collections.get(filename)
    if collection is None:
        return jsonify({"response": "PDF not found. Please upload a PDF first."}), 404

    try:
        context = retrieve_context(collection, user_message)

        if not context:
            return jsonify({"response": "I couldn't find any relevant information in the document."})
        answer = ask_llm(context, user_message)
        return jsonify({"response": answer})

    except Exception as e:
        print(f"Error in chat: {e}")
        return jsonify({"response": f"Sorry, an error occurred while processing your question. Please try again."}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))