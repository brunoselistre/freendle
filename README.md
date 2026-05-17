# Book Scraper

A minimal web application to search for books on [Anna's Archive](https://annas-archive.org), download them, and catalog them via the Calibre CLI.

The app features a lightweight, single-page frontend and a Python backend that handles scraping and downloading.

## Features

- **Search**: Look up books by title with prioritized format fallback (`azw3` → `pdf` → `epub`).
- **Scrape**: Handles JavaScript-rendered pages using Playwright.
- **Download**: Securely downloads files to an isolated sandbox directory.
- **Catalog**: Integrates with the Calibre CLI to manage your book collection.

## Tech Stack

- **Backend**: Python 3.11, FastAPI, Playwright, httpx
- **Frontend**: Vite, plain HTML/CSS/JS (no heavy frameworks)
- **Containerization**: Docker (multi-stage build)
- **Testing**: pytest (backend), Playwright test runner (e2e)

## Project Structure

```text
├── backend/
│   ├── src/
│   │   ├── scraper.py       # Anna's Archive search + detail page scraping
│   │   ├── downloader.py    # File download to sandbox
│   │   └── api.py           # FastAPI application routes
│   ├── tests/
│   │   ├── unit/
│   │   │   ├── test_scraper.py
│   │   │   └── test_downloader.py
│   │   └── integration/
│   │       └── test_api.py
│   ├── pyproject.toml
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── src/
│   │   ├── main.ts          # Search + download orchestration
│   │   └── api.ts           # Backend API client
│   ├── package.json
│   └── vite.config.ts
├── downloads/               # Sandbox download directory (gitignored)
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) (Recommended)
- or Python 3.11+ and Node.js 20+

## Getting Started

### 1. Clone the Repository

```bash
git clone <repository-url>
cd bookstore
```

### 2. Environment Configuration

Copy the example environment file and fill in your details:

```bash
cp .env.example .env
```

Edit `.env` to set your credentials and paths:

```env
# Required
BOOKSTORE_DOWNLOAD_DIR=/app/downloads

# Optional: For Gmail integration (to send books to Kindle)
BOOKSTORE_KINDLE_EMAIL=your-kindle@kindle.com
BOOKSTORE_GMAIL_SENDER=you@gmail.com
BOOKSTORE_GMAIL_CREDENTIALS_PATH=/app/gmail_credentials.json
BOOKSTORE_GMAIL_TOKEN_PATH=/app/gmail_token.json

# CORS (default allows all)
BOOKSTORE_BACKEND_CORS_ORIGINS=[]
```

### 3. Run with Docker

The simplest way to get started is using Docker Compose:

```bash
docker-compose up --build
```

The application will be available at `http://localhost:8000`.

### 4. Run Locally (Development)

#### Backend

```bash
cd backend

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium

# Run the server
uvicorn src.api:app --reload --port 8000
```

#### Frontend

```bash
cd frontend

# Install dependencies
npm install

# Start the dev server
npm run dev
```

The frontend will be available at `http://localhost:5173` (or the port shown in your terminal).

## Usage

1. Open the application in your browser.
2. Enter a book title in the search bar and click **Search**.
3. Browse the results, which display the title, author, format, file size, and language.
4. Click **Download** on a result to save the file to the configured download directory.

## Testing

Run the backend test suite with pytest:

```bash
cd backend
pytest
```

To run integration tests:

```bash
pytest tests/integration/
```

## Deployment

The included `Dockerfile` is optimized for production with a multi-stage build that bundles the frontend assets and serves them statically via the FastAPI backend.

Build the production image:

```bash
docker build -t bookstore:latest .
```

Run the container:

```bash
docker run -p 8000:8000 --env-file .env bookstore:latest
```

## License

This project is for personal use. Please ensure your usage complies with the terms of service of Anna's Archive and your local copyright laws.
