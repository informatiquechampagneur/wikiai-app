from fastapi import FastAPI, APIRouter, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid
from datetime import datetime, timezone
import asyncio
import re
from fastapi.responses import StreamingResponse
from fastapi import UploadFile, File, Form
from io import BytesIO
from docx import Document
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from pptx import Presentation
from openpyxl import Workbook
import requests
import json

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create the main app
app = FastAPI()
api_router = APIRouter(prefix="/api")

# Models (même que avant)
class ChatMessage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    message: str
    response: str
    message_type: str
    trust_score: Optional[float] = None
    sources: Optional[List[str]] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class ChatRequest(BaseModel):
    message: str
    message_type: str
    session_id: Optional[str] = None

class DocumentRequest(BaseModel):
    content: str
    title: str = "Document WikiAI"
    format: str = "pdf"
    filename: Optional[str] = None

# IA GRATUITE avec Hugging Face
async def get_ai_response_free(message: str, message_type: str) -> dict:
    """IA gratuite avec Hugging Face"""
    try:
        # Configuration selon le type de message
        system_prompts = {
            "je_veux": "Réponds comme un assistant éducatif québécois spécialisé dans l'aide aux étudiants.",
            "je_recherche": "Aide à la recherche d'informations éducatives pour étudiants québécois.",
            "sources_fiables": "Recommande des sources fiables québécoises et canadiennes (.gouv.qc.ca, .edu).",
            "activites": "Crée des activités pédagogiques adaptées au programme scolaire québécois."
        }
        
        # Construire le prompt complet
        full_prompt = f"{system_prompts.get(message_type, system_prompts['je_veux'])}\n\nQuestion: {message}\n\nRéponse:"
        
        # API Hugging Face - Modèle gratuit
        API_URL = "https://api-inference.huggingface.co/models/microsoft/DialoGPT-medium"
        headers = {"Authorization": f"Bearer {os.environ.get('HUGGINGFACE_TOKEN')}"}
        
        payload = {
            "inputs": full_prompt,
            "parameters": {
                "max_length": 300,
                "temperature": 0.7,
                "pad_token_id": 50256
            }
        }
        
        # Fallback responses si API indisponible
        fallback_responses = {
            "je_veux": f"Voici des informations sur votre question '{message}'. Pour des réponses plus détaillées, consultez les ressources éducatives québécoises officielles sur quebec.ca ou education.gouv.qc.ca.",
            "je_recherche": f"Pour rechercher des informations sur '{message}', je recommande de consulter les sources fiables québécoises comme la Bibliothèque nationale du Québec (banq.qc.ca) et les sites gouvernementaux (.gouv.qc.ca).",
            "sources_fiables": f"Sources recommandées pour '{message}': 1) Sites gouvernementaux (.gouv.qc.ca) 2) Universités québécoises (.ca) 3) Bibliothèque nationale (banq.qc.ca). Évitez les sources non vérifiées.",
            "activites": f"Activité pédagogique sur '{message}': Créez un projet de recherche en utilisant les ressources officielles québécoises, incluez une bibliographie avec des sources .gov.qc.ca et préparez une présentation."
        }
        
        try:
            response = requests.post(API_URL, headers=headers, json=payload, timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                if isinstance(result, list) and len(result) > 0:
                    ai_response = result[0].get('generated_text', '').replace(full_prompt, '').strip()
                    if len(ai_response) < 20:  # Réponse trop courte
                        ai_response = fallback_responses.get(message_type, fallback_responses['je_veux'])
                else:
                    ai_response = fallback_responses.get(message_type, fallback_responses['je_veux'])
            else:
                ai_response = fallback_responses.get(message_type, fallback_responses['je_veux'])
                
        except Exception:
            # Si l'API Hugging Face échoue, utiliser les réponses de fallback
            ai_response = fallback_responses.get(message_type, fallback_responses['je_veux'])
        
        return {
            "response": ai_response,
            "trust_score": 0.85 if message_type == "sources_fiables" else None,
            "sources": ["Sources éducatives québécoises recommandées"],
            "can_download": len(ai_response) > 50
        }
        
    except Exception as e:
        logging.error(f"Erreur IA: {e}")
        return {
            "response": "Service temporairement indisponible. Votre question a été notée, veuillez réessayer dans quelques instants.",
            "trust_score": None,
            "sources": []
        }

# Routes principales
@api_router.get("/")
async def root():
    return {"message": "WikiAI Free - Assistant IA Gratuit pour les étudiants québécois"}

@api_router.post("/chat", response_model=ChatMessage)
async def chat_with_ai(request: ChatRequest):
    try:
        session_id = request.session_id or str(uuid.uuid4())
        
        # Utiliser l'IA gratuite
        ai_result = await get_ai_response_free(request.message, request.message_type)
        
        chat_message = ChatMessage(
            session_id=session_id,
            message=request.message,
            response=ai_result["response"],
            message_type=request.message_type,
            trust_score=ai_result["trust_score"],
            sources=ai_result["sources"]
        )
        
        # Sauvegarder en base
        await db.chat_messages.insert_one(chat_message.dict())
        
        return chat_message
        
    except Exception as e:
        logging.error(f"Erreur chat: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors du traitement")

@api_router.get("/subjects")
async def get_school_subjects():
    """Matières du système éducatif québécois"""
    subjects = {
        "langues": {
            "name": "Langues",
            "subjects": ["Français", "Anglais", "Espagnol"]
        },
        "sciences": {
            "name": "Sciences & Mathématiques", 
            "subjects": ["Mathématiques", "Sciences et technologies"]
        },
        "sciences_humaines": {
            "name": "Sciences Humaines",
            "subjects": ["Histoire", "Géographie", "Monde contemporain"]
        },
        "arts": {
            "name": "Arts",
            "subjects": ["Arts plastiques", "Musique", "Art dramatique"]
        }
    }
    return subjects

# Génération de documents (version simplifiée)
def generate_pdf_document_simple(title: str, content: str) -> BytesIO:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    
    story = []
    story.append(Paragraph(title, styles['Title']))
    story.append(Paragraph(content.replace('\n', '<br/>'), styles['Normal']))
    
    doc.build(story)
    buffer.seek(0)
    return buffer

@api_router.post("/generate-document")
async def generate_document_simple(request: DocumentRequest):
    try:
        if request.format == 'pdf':
            buffer = generate_pdf_document_simple(request.title, request.content)
            media_type = "application/pdf"
            filename = f"{request.title.replace(' ', '_')}.pdf"
        else:
            raise HTTPException(status_code=400, detail="Format PDF uniquement en version gratuite")
        
        return StreamingResponse(
            BytesIO(buffer.read()),
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Include router
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
