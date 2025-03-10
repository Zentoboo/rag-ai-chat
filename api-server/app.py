from flask import Flask, request, jsonify
from flask_cors import CORS
import json
import os
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Configuration
WEBHOOK_URL = os.environ.get('WEBHOOK_URL')
PORT = int(os.environ.get('PORT', '5000'))
DEBUG = os.environ.get('DEBUG', 'False').lower() == 'true'

@app.route('/api/chat', methods=['POST'])
def chat():
    """
    Endpoint to handle chat requests from the frontend
    """
    try:
        data = request.json
        user_message = data.get('message', '')
        session_id = data.get('session_id', 'default')
        
        if not user_message:
            return jsonify({'error': 'No message provided'}), 400
        
        # Prepare the payload for your existing webhook system
        payload = {
            'message': user_message,
            'session_id': session_id,
        }
        
        # Forward the request to your existing webhook handler
        response = requests.post(WEBHOOK_URL, json=payload)
        
        if response.status_code != 200:
            return jsonify({'error': 'Webhook processing failed', 'details': response.text}), 500
        
        # Return the response from your AI system
        return jsonify(response.json())
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    """
    Simple health check endpoint
    """
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    print(f"Starting API server on port {PORT}...")
    app.run(host='0.0.0.0', port=PORT, debug=DEBUG)