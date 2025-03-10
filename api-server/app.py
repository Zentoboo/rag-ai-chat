from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
import json
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Configuration - use container name instead of localhost
WEBHOOK_HOST = os.environ.get('WEBHOOK_HOST', 'webhook-server')  # Container name
WEBHOOK_PORT = os.environ.get('WEBHOOK_PORT', '5678')
WEBHOOK_URL = f"http://{WEBHOOK_HOST}:{WEBHOOK_PORT}/webhook"
PORT = int(os.environ.get('PORT', '5000'))
DEBUG = os.environ.get('DEBUG', 'False').lower() == 'true'

@app.route('/api/chat', methods=['POST'])
def chat():
    """
    Endpoint to handle chat requests from the frontend
    """
    try:
        data = request.json
        print(f"Received chat request: {data}")
        
        user_message = data.get('message', '')
        session_id = data.get('session_id', 'default')
        
        if not user_message:
            return jsonify({'error': 'No message provided'}), 400
        
        # Prepare the payload for your webhook
        payload = {
            'message': user_message,
            'session_id': session_id,
        }
        
        print(f"Forwarding request to webhook at {WEBHOOK_URL}")
        # Forward the request to your webhook handler with increased timeout
        response = requests.post(
            WEBHOOK_URL, 
            json=payload,
            timeout=90  # Increased timeout for Docker networking and model inference
        )
        
        if response.status_code != 200:
            print(f"Error from webhook: {response.text}")
            return jsonify({'error': 'Webhook processing failed', 'details': response.text}), 500
        
        # Return the response from your AI system
        return jsonify(response.json())
    
    except requests.RequestException as e:
        print(f"Request exception: {str(e)}")
        return jsonify({'error': f'Connection error: {str(e)}'}), 500
    except Exception as e:
        print(f"General exception: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    """
    Simple health check endpoint
    """
    return jsonify({'status': 'ok'})

# Add a diagnostic endpoint to check connectivity
@app.route('/api/diagnose', methods=['GET'])
def diagnose():
    """
    Diagnostic endpoint to check connections to other services
    """
    results = {
        'api_server': 'ok',
        'webhook_server': 'unknown'
    }
    
    # Check webhook connection
    try:
        webhook_response = requests.get(f"http://{WEBHOOK_HOST}:{WEBHOOK_PORT}/health", timeout=5)
        if webhook_response.status_code == 200:
            results['webhook_server'] = 'ok'
        else:
            results['webhook_server'] = f'error: status {webhook_response.status_code}'
    except Exception as e:
        results['webhook_server'] = f'error: {str(e)}'
    
    return jsonify(results)

if __name__ == '__main__':
    print(f"Starting API server on port {PORT}...")
    print(f"Webhook URL: {WEBHOOK_URL}")
    app.run(host='0.0.0.0', port=PORT, debug=DEBUG)