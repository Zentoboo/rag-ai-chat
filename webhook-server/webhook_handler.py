from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import json
import os
import httpx
import asyncio
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Configuration
# These environment variables should use container names rather than localhost in Docker
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "ollama")  # Container name for Ollama
OLLAMA_PORT = os.environ.get("OLLAMA_PORT", "11434")
OLLAMA_API_URL = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/generate"
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama2")

QDRANT_HOST = os.environ.get("QDRANT_HOST", "qdrant")  # Container name for Qdrant
QDRANT_PORT = os.environ.get("QDRANT_PORT", "6333")
QDRANT_API_URL = f"http://{QDRANT_HOST}:{QDRANT_PORT}"
COLLECTION_NAME = os.environ.get("COLLECTION_NAME", "documents")

# PostgreSQL connection should use container name
DB_HOST = os.environ.get("DB_HOST", "postgres")  # Container name for PostgreSQL
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_USER = os.environ.get("DB_USER", "username")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "password")
DB_NAME = os.environ.get("DB_NAME", "chat_memory")
POSTGRES_CONNECTION = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Server port
PORT = int(os.environ.get("PORT", "5678"))

# Function to retrieve context from Qdrant vector database
async def get_context_from_qdrant(query: str) -> str:
    try:
        print(f"Getting embeddings from Ollama at {OLLAMA_API_URL.replace('generate', 'embeddings')}")
        async with httpx.AsyncClient() as client:
            # Convert query to embedding using Ollama
            embedding_response = await client.post(
                f"{OLLAMA_API_URL.replace('generate', 'embeddings')}",
                json={"prompt": query, "model": OLLAMA_MODEL},
                timeout=30.0  # Increased timeout for Docker networking
            )
            
            if embedding_response.status_code != 200:
                print(f"Error getting embeddings: {embedding_response.text}")
                return ""
                
            embedding = embedding_response.json()["embedding"]
            
            print(f"Searching Qdrant at {QDRANT_API_URL}/collections/{COLLECTION_NAME}/points/search")
            # Search Qdrant for similar documents
            search_response = await client.post(
                f"{QDRANT_API_URL}/collections/{COLLECTION_NAME}/points/search",
                json={
                    "vector": embedding,
                    "limit": 3,
                    "with_payload": True
                },
                timeout=30.0  # Increased timeout for Docker networking
            )
            
            if search_response.status_code != 200:
                print(f"Error searching Qdrant: {search_response.text}")
                return ""
                
            results = search_response.json().get("result", [])
            
            if not results:
                return ""
            
            # Concatenate document texts to form context
            context = "\n\n".join([result["payload"].get("text", "") for result in results if "payload" in result and "text" in result["payload"]])
            return context
    except Exception as e:
        print(f"Error retrieving context: {str(e)}")
        return ""

# Function to save chat to PostgreSQL
async def save_chat_to_postgres(session_id: str, user_message: str, ai_response: str) -> None:
    try:
        import psycopg2
        
        print(f"Connecting to PostgreSQL at {DB_HOST}:{DB_PORT}")
        # Connect to the PostgreSQL database
        conn = psycopg2.connect(POSTGRES_CONNECTION)
        cursor = conn.cursor()
        
        # Create table if not exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id SERIAL PRIMARY KEY,
                session_id VARCHAR(100) NOT NULL,
                user_message TEXT NOT NULL,
                ai_response TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Insert the chat message
        cursor.execute("""
            INSERT INTO chat_history (session_id, user_message, ai_response)
            VALUES (%s, %s, %s)
        """, (session_id, user_message, ai_response))
        
        # Commit the transaction
        conn.commit()
        
        # Close the cursor and connection
        cursor.close()
        conn.close()
        
    except Exception as e:
        print(f"Error saving to PostgreSQL: {str(e)}")

# N8n specific webhook handler route
@app.post("/webhook-test/invoke_n8n_agent")
async def n8n_webhook_handler(request: Request):
    try:
        # Get the JSON data from the request
        data = await request.json()
        print(f"Received webhook from n8n: {data}")
        
        # Extract fields from n8n payload
        user_message = data.get('message', '')
        session_id = data.get('session_id', 'default')
        document_context = data.get('document_context', '')
        
        # If no message is provided, return an error
        if not user_message:
            return {"error": "No message provided", "success": False}
        
        # Get additional context from Qdrant if needed and if document_context is not provided
        context = document_context
        if not context:
            context = await get_context_from_qdrant(user_message)
        
        # Create prompt with context and user message
        prompt = f"""You are a helpful AI assistant with access to a knowledge base.
        
Context information from documents:
{context}

User question: {user_message}

Provide a helpful, accurate response based on the context information. If the question cannot be answered based on the provided context, say so politely."""
        
        print(f"Sending prompt to Ollama at {OLLAMA_API_URL}")
        # Send prompt to Ollama for generation
        async with httpx.AsyncClient() as client:
            response = await client.post(
                OLLAMA_API_URL,
                json={
                    "prompt": prompt,
                    "model": OLLAMA_MODEL,
                    "stream": False
                },
                timeout=60.0  # Increased timeout for model inference
            )
            
            if response.status_code != 200:
                print(f"Error from Ollama: {response.text}")
                return {"error": "Error generating response from Ollama", "success": False}
                
            ai_response = response.json().get("response", "I couldn't generate a response.")
        
        # Save the conversation to PostgreSQL
        await save_chat_to_postgres(session_id, user_message, ai_response)
        
        # Return the AI response in the format expected by n8n
        return {
            "response": ai_response, 
            "session_id": session_id,
            "success": True
        }
        
    except Exception as e:
        print(f"Error processing webhook from n8n: {str(e)}")
        return {"error": str(e), "success": False}

# Original webhook endpoint for direct API calls
@app.post("/webhook")
async def webhook_handler(request: Request):
    try:
        # Get the JSON data from the request
        data = await request.json()
        
        # Extract message and session_id
        user_message = data.get('message', '')
        session_id = data.get('session_id', 'default')
        
        if not user_message:
            raise HTTPException(status_code=400, detail="No message provided")
        
        # Get relevant context from vector database
        context = await get_context_from_qdrant(user_message)
        
        # Create prompt with context and user message
        prompt = f"""You are a helpful AI assistant with access to a knowledge base.
        
Context information from documents:
{context}

User question: {user_message}

Provide a helpful, accurate response based on the context information. If the question cannot be answered based on the provided context, say so politely."""
        
        # Send prompt to Ollama for generation
        async with httpx.AsyncClient() as client:
            response = await client.post(
                OLLAMA_API_URL,
                json={
                    "prompt": prompt,
                    "model": OLLAMA_MODEL,
                    "stream": False
                },
                timeout=60.0  # Increased timeout for model inference
            )
            
            if response.status_code != 200:
                raise HTTPException(status_code=500, detail="Error generating response from Ollama")
                
            ai_response = response.json().get("response", "I couldn't generate a response.")
        
        # Save the conversation to PostgreSQL
        await save_chat_to_postgres(session_id, user_message, ai_response)
        
        # Return the AI response
        return {"response": ai_response, "session_id": session_id}
        
    except Exception as e:
        print(f"Error processing webhook: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Health check endpoint
@app.get("/health")
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    print(f"Starting webhook server on port {PORT}...")
    print(f"Ollama API URL: {OLLAMA_API_URL}")
    print(f"Qdrant API URL: {QDRANT_API_URL}")
    print(f"PostgreSQL Connection: {DB_HOST}:{DB_PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)