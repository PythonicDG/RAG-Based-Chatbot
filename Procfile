release: python -c "from models import init_db; init_db()"
web: gunicorn -w 1 -k uvicorn.workers.UvicornWorker app:app --bind 0.0.0.0:$PORT