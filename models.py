import uuid
from datetime import datetime
from sqlalchemy import create_engine, Column, String, Integer, Float, DateTime, Text, ForeignKey
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

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
    return uuid.uuid4().hex


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    bots = relationship("Bot", back_populates="owner", cascade="all, delete-orphan")


class Bot(Base):
    __tablename__ = "bots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(100), nullable=False, default="My Chatbot")
    welcome_message = Column(String(500), default="Hi there! 👋 Ask me anything about the document.")
    primary_color = Column(String(7), default="#6C63FF")
    api_key = Column(String(64), unique=True, default=generate_api_key, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="bots")
    documents = relationship("Document", back_populates="bot", cascade="all, delete-orphan")
    chat_logs = relationship("ChatLog", back_populates="bot", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bot_id = Column(Integer, ForeignKey("bots.id"), nullable=False)
    filename = Column(String(255), nullable=False)
    original_name = Column(String(255), nullable=False)
    chunk_count = Column(Integer, default=0)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    bot = relationship("Bot", back_populates="documents")


class ChatLog(Base):
    __tablename__ = "chat_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bot_id = Column(Integer, ForeignKey("bots.id"), nullable=False)
    session_id = Column(String(64), nullable=True)
    user_message = Column(Text, nullable=False)
    bot_response = Column(Text, nullable=False)
    response_time_ms = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    bot = relationship("Bot", back_populates="chat_logs")


def init_db():
    import os
    os.makedirs("instance", exist_ok=True)
    Base.metadata.create_all(engine)
