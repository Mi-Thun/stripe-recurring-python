#!/bin/bash

# Stripe SaaS Dashboard Application
# This script runs the complete dashboard with authentication, plans, and analytics

echo "🚀 Starting Stripe SaaS Dashboard..."

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install/update dependencies
echo "📋 Installing dependencies..."
pip install -r requirements.txt

# Check if .env file exists
if [ ! -f ".env" ]; then
    echo "⚠️  Warning: .env file not found. Please copy .env.example to .env and configure it."
    exit 1
fi

# Run database migrations/setup
echo "🗄️  Setting up database..."
python3 -c "
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv('PSQL_DB_URL')

try:
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    cur = conn.cursor()
    
    # Create app_users table if it doesn't exist
    cur.execute('''
        CREATE TABLE IF NOT EXISTS app_users (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            email VARCHAR(255) UNIQUE NOT NULL,
            password_hash VARCHAR(256) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    
    conn.commit()
    print('✅ Database setup complete')
except Exception as e:
    print(f'❌ Database setup failed: {e}')
finally:
    if 'cur' in locals():
        cur.close()
    if 'conn' in locals():
        conn.close()
"

echo ""
echo "🌟 Dashboard Features:"
echo "   • User Authentication (Login/Signup)"
echo "   • Subscription Plans Management"
echo "   • Plan Change Analytics"
echo "   • Proration Tracking"
echo "   • Billing History"
echo "   • Customer Portal Integration"
echo ""
echo "📱 Available Routes:"
echo "   • http://localhost:4242/ - Home page"
echo "   • http://localhost:4242/login - User login"
echo "   • http://localhost:4242/signup - User registration" 
echo "   • http://localhost:4242/plans - Available plans"
echo "   • http://localhost:4242/dashboard - User dashboard"
echo "   • http://localhost:4242/analytics - Advanced analytics"
echo "   • http://localhost:4242/profile - User profile"
echo "   • http://localhost:4242/about - About page"
echo ""
echo "🔧 For Stripe webhook testing, run in another terminal:"
echo "   stripe listen --forward-to localhost:4242/webhook"
echo ""

# Start the Flask application
python3 app.py
