from fastapi import FastAPI, HTTPException, Request
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

# Configuration
OLLAMA_API_URL = os.environ.get("OLLAMA_API_URL")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL")
QDRANT_API_URL = os.environ.get("QDRANT_API_URL")
COLLECTION_NAME = os.environ.get("COLLECTION_NAME")
POSTGRES_CONNECTION = os.environ.get("POSTGRES_CONNECTION")
PORT = int(os.environ.get("PORT", "8000"))

# Model for the incoming chat request
class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"
    user_id: Optional[str] = None

# Function to retrieve context from Qdrant vector database
async def get_context_from_qdrant(query: str) -> str:
    try:
        async with httpx.AsyncClient() as client:
            # Convert query to embedding using Ollama
            embedding_response = await client.post(
                f"{OLLAMA_API_URL.replace('generate', 'embeddings')}",
                json={"prompt": query, "model": OLLAMA_MODEL}
            )
            
            if embedding_response.status_code != 200:
                print(f"Error getting embeddings: {embedding_response.text}")
                return ""
                
            embedding = embedding_response.json()["embedding"]
            
            # Search Qdrant for similar documents
            search_response = await client.post(
                f"{QDRANT_API_URL}/collections/{COLLECTION_NAME}/points/search",
                json={
                    "vector": embedding,
                    "limit": 3,
                    "with_payload": True
                }
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

@app.post("/webhook")
async def webhook_handler(request: ChatRequest):
    try:
        # Get relevant context from vector database
        context = await get_context_from_qdrant(request.message)
        
        # Create prompt with context and user message
        prompt = f"""You are a helpful AI assistant with access to a knowledge base.
        
Context information from documents:
{context}

User question: {request.message}

Provide a helpful, accurate response based on the context information. If the question cannot be answered based on the provided context, say so politely."""
        
        # Send prompt to Ollama for generation
        async with httpx.AsyncClient() as client:
            response = await client.post(
                OLLAMA_API_URL,
                json={
                    "prompt": prompt,
                    "model": OLLAMA_MODEL,
                    "stream": False
                }
            )
            
            if response.status_code != 200:
                raise HTTPException(status_code=500, detail="Error generating response from Ollama")
                
            ai_response = response.json().get("response", "I couldn't generate a response.")
        
        # Save the conversation to PostgreSQL
        await save_chat_to_postgres(request.session_id, request.message, ai_response)
        
        # Return the AI response
        return {"response": ai_response, "session_id": request.session_id}
        
    except Exception as e:
        print(f"Error processing webhook: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    print(f"Starting webhook server on port {PORT}...")
    uvicorn.run(app, host="0.0.0.0", port=PORT)