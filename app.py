import os
from google.colab import userdata
from groq import Groq
import chromadb
from chromadb.utils import embedding_functions

client = Groq(api_key=os.environ.get('GROQ_API'))

chroma_client = chromadb.Client()

emb_fn = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")

try:
    chroma_client.delete_collection("hr_handbook")
except:
    pass 

collection = chroma_client.create_collection(name='hr_handbook', embedding_function=emb_fn)


