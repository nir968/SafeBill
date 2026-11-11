# SafeBill - Smart Financial Management 💡

SafeBill is an advanced full-stack application combining modern architecture with artificial intelligence and automation to manage, scan, and centralize financial documents in a secure and accessible platform.

## ✨ Core Features

### 🤖 AI, Agents & Document Processing
* **Smart Scanning (Vision/OCR):** Upload invoices, receipts, or PDF documents to automatically extract critical data such as vendor name, amount, due date, and category.
* **AI Agents:** Integrated language models and automated agents that handle data processing, support smart queries regarding account status, and provide automated responses.
* **Dynamic Data Updates:** Management interface with interactive modals for updating fields and statuses, syncing directly with the backend API and database.

### 🌐 Web Dashboard
* **Clean UI:** A modern, responsive dashboard featuring financial summaries, payment due alerts, and tracking tables.
* **Cloud Ready:** Configured for seamless deployment, ensuring accessible management across environments.
* **Real-time Control:** Clear status tracking (paid, pending, reports), drag-and-drop file uploads, and expense monitoring.

### 🔒 Security
* **Robust Architecture:** Designed with advanced security controls, including token-based authentication, prevention of SQL injections and prompt injections, and sensitive data encryption.

## 🛠️ Tech Stack
* **Backend:** FastAPI, Python
* **Database & Caching:** PostgreSQL, Redis
* **AI & Vision:** Google Gemini (Vision & Agent Reasoning)
* **Integrations:** Telegram Bot API
* **Infrastructure:** Docker, Docker Compose

---

## 🚀 Getting Started & Docker Operations - Prerequisites


###  Ensure Docker and Docker Compose are installed on your machine.
#  Copy the environment variables template and configure your local secrets:
   ```bash
   cp .env.example .env
   ```


#  Start the system in the background (detached mode)
```bash
docker compose up -d
```
#  Stop and safely remove containers, networks, and default volumes
```bash
docker compose down
```

<img width="2541" height="1231" alt="Screenshot_30" src="https://github.com/user-attachments/assets/b4d4ccaa-0e11-43c6-a8a3-6d21f9d43799" />
<img width="2524" height="1241" alt="Screenshot_28" src="https://github.com/user-attachments/assets/fe851ea3-e553-4128-977d-636e450e5881" />
