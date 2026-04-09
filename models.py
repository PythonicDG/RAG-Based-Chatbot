import uuid
from datetime import datetime
from sqlalchemy import create_engine, Column, String, Integer, Float, DateTime, Text, ForeignKey
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

# Default SQLite database for local development and small deployments.
# For specialized high-scale environments, consider migrating to PostgreSQL.
DATABASE_URL = "sqlite:///instance/app.db"

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def generate_api_key():
    """Generates a unique API key for bots."""
    return uuid.uuid4().hex


class User(Base):
    """
    Represents a registered user who can create and manage their own chatbots.
    Stored in the 'users' table.
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationship: A user can own multiple chatbots.
    bots = relationship("Bot", back_populates="owner", cascade="all, delete-orphan")


class Bot(Base):
    """
    Represents an individual chatbot instance with its own configuration and document store.
    Stored in the 'bots' table.
    """
    __tablename__ = "bots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(100), nullable=False, default="My Chatbot")
    welcome_message = Column(String(500), default="Hi there! 👋 Ask me anything about the document.")
    primary_color = Column(String(7), default="#6C63FF")
    api_key = Column(String(64), unique=True, default=generate_api_key, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    owner = relationship("User", back_populates="bots")
    documents = relationship("Document", back_populates="bot", cascade="all, delete-orphan")
    chat_logs = relationship("ChatLog", back_populates="bot", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "welcome_message": self.welcome_message,
            "primary_color": self.primary_color,
            "api_key": self.api_key,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "document_count": len(self.documents)
        }


class Document(Base):
    """
    Represents a PDF document that has been uploaded and processed for a specific bot.
    Stored in the 'documents' table.
    """
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bot_id = Column(Integer, ForeignKey("bots.id"), nullable=False)
    filename = Column(String(255), nullable=False) # The unique filename on disk
    original_name = Column(String(255), nullable=False) # The original filename provided by the user
    chunk_count = Column(Integer, default=0)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    # Relationship to the parent bot
    bot = relationship("Bot", back_populates="documents")


class ChatLog(Base):
    """
    Stores individual chat messages, responses, and performance telemetry.
    Stored in the 'chat_logs' table.
    """
    __tablename__ = "chat_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bot_id = Column(Integer, ForeignKey("bots.id"), nullable=False)
    session_id = Column(String(64), nullable=True) # Used to track conversations across messages
    user_message = Column(Text, nullable=False)
    bot_response = Column(Text, nullable=False)
    response_time_ms = Column(Float, nullable=True) # Performance tracking (latency)
    language = Column(String(10), default="en", nullable=True) # The language code used for the chat
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationship to the parent bot
    bot = relationship("Bot", back_populates="chat_logs")


def init_db():
    import os
    import sqlite3
    # Ensure the instance directory exists for the SQLite database file
    os.makedirs("instance", exist_ok=True)
    Base.metadata.create_all(engine)
    
    # Auto-migration: Check and add missing columns for existing SQLite databases
    if "sqlite" in DATABASE_URL:
        db_path = DATABASE_URL.replace("sqlite:///", "")
        if os.path.exists(db_path):
            try:
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                cursor.execute("PRAGMA table_info(chat_logs)")
                columns = [column[1] for column in cursor.fetchall()]
                if "language" not in columns:
                    cursor.execute("ALTER TABLE chat_logs ADD COLUMN language TEXT DEFAULT 'en'")
                conn.commit()
                conn.close()
            except Exception:
                pass
