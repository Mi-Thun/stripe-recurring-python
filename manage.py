#!/usr/bin/env python3
"""
Management Command Utility for Stripe Integration App

This script provides various utility functions for database management,
data cleanup, and other administrative tasks.

Usage:
    python manage.py <command> [options]

Commands:
    list-tables         - List all database tables
    clear-tables        - Clear all data from all tables
    clear-table <name>  - Clear data from specific table
    show-stats          - Show database statistics
    sync-stripe         - Sync data from Stripe
    reset-database      - Reset database to initial state
    create-admin        - Create admin user
    backup-data         - Backup database data
    restore-data        - Restore database data
    check-syntax        - Check Flask app syntax
    test-db             - Test database connection
    show-env            - Show environment variables status
    help                - Show this help message
"""

import os
import sys
import json
import argparse
from datetime import datetime
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor
import stripe

# Load environment variables
load_dotenv()

# Database configuration
DATABASE_URL = os.getenv('PSQL_DB_URL')
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')

def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def list_tables():
    """List all database tables"""
    print("📋 Listing all database tables...")
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT table_name, table_type
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name;
        """)
        tables = cur.fetchall()
        
        if tables:
            print(f"\nFound {len(tables)} tables:")
            print("-" * 50)
            for table in tables:
                print(f"• {table['table_name']} ({table['table_type']})")
        else:
            print("No tables found in the database.")
    except Exception as e:
        print(f"❌ Error listing tables: {e}")
    finally:
        cur.close()
        conn.close()

def show_table_stats():
    """Show statistics for all tables"""
    print("📊 Database Statistics:")
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Get list of tables
        cur.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name;
        """)
        tables = [row['table_name'] for row in cur.fetchall()]
        
        print("-" * 60)
        print(f"{'Table Name':<25} {'Row Count':<15} {'Size':<15}")
        print("-" * 60)
        
        total_rows = 0
        for table in tables:
            try:
                # Get row count
                cur.execute(f"SELECT COUNT(*) as count FROM {table}")
                count = cur.fetchone()['count']
                total_rows += count
                
                # Get table size (handle potential permission issues)
                try:
                    cur.execute("""
                        SELECT pg_size_pretty(pg_total_relation_size(%s)) as size
                    """, (table,))
                    size = cur.fetchone()['size']
                except Exception:
                    size = "N/A"
                
                print(f"{table:<25} {count:<15} {size:<15}")
            except Exception as e:
                print(f"{table:<25} {'ERROR':<15} {str(e)[:10]:<15}")
        
        print("-" * 60)
        print(f"{'TOTAL ROWS':<25} {total_rows:<15}")
        
    except Exception as e:
        print(f"❌ Error getting statistics: {e}")
    finally:
        cur.close()
        conn.close()

def clear_all_tables():
    """Clear all data from all tables"""
    print("🗑️  Clearing all table data...")
    
    # Confirm action
    confirm = input("⚠️  This will delete ALL data from ALL tables. Are you sure? (type 'yes' to confirm): ")
    if confirm.lower() != 'yes':
        print("❌ Operation cancelled.")
        return
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Get list of tables in dependency order (to handle foreign keys)
        cur.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name;
        """)
        tables = [row['table_name'] for row in cur.fetchall()]
        
        # Define table clearing order to handle foreign key dependencies
        dependent_tables = [
            'subscription_items',
            'invoice_line_items', 
            'invoice_items',
            'subscriptions',
            'invoices',
            'charges',
            'payment_intents',
            'payment_methods',
            'prices',
            'products',
            'customers',
            'stripe_events',
            'event_processing_log',
            'app_users'
        ]
        
        # Clear tables in dependency order
        cleared_count = 0
        for table in dependent_tables:
            if table in tables:
                try:
                    cur.execute(f"DELETE FROM {table};")
                    rows_affected = cur.rowcount
                    print(f"✅ Cleared table: {table} ({rows_affected} rows deleted)")
                    cleared_count += 1
                except Exception as e:
                    print(f"❌ Error clearing table {table}: {e}")
        
        # Clear any remaining tables not in the dependency list
        for table in tables:
            if table not in dependent_tables:
                try:
                    cur.execute(f"DELETE FROM {table};")
                    rows_affected = cur.rowcount
                    print(f"✅ Cleared table: {table} ({rows_affected} rows deleted)")
                    cleared_count += 1
                except Exception as e:
                    print(f"❌ Error clearing table {table}: {e}")
        
        conn.commit()
        print(f"\n✅ Successfully cleared {cleared_count} tables")
        
    except Exception as e:
        conn.rollback()
        print(f"❌ Error clearing tables: {e}")
    finally:
        cur.close()
        conn.close()

def clear_specific_table(table_name):
    """Clear data from specific table"""
    print(f"🗑️  Clearing data from table: {table_name}")
    
    # Confirm action
    confirm = input(f"⚠️  This will delete ALL data from table '{table_name}'. Are you sure? (type 'yes' to confirm): ")
    if confirm.lower() != 'yes':
        print("❌ Operation cancelled.")
        return
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Check if table exists
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = %s
            );
        """, (table_name,))
        
        if not cur.fetchone()[0]:
            print(f"❌ Table '{table_name}' does not exist.")
            return
        
        # Clear the table
        cur.execute(f"TRUNCATE TABLE {table_name} CASCADE;")
        conn.commit()
        print(f"✅ Successfully cleared table: {table_name}")
        
    except Exception as e:
        conn.rollback()
        print(f"❌ Error clearing table {table_name}: {e}")
    finally:
        cur.close()
        conn.close()

def sync_stripe_data():
    """Sync data from Stripe"""
    print("🔄 Syncing data from Stripe...")
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Import upsert functions from app
        sys.path.append(os.path.dirname(os.path.abspath(__file__)))
        from app import upsert_customer, upsert_product, upsert_price, upsert_subscription_new
        
        # Sync customers
        print("📥 Syncing customers...")
        customers = stripe.Customer.list(limit=100)
        customer_count = 0
        for customer in customers.data:
            try:
                upsert_customer(customer, conn, cur)
                customer_count += 1
            except Exception as e:
                print(f"❌ Error syncing customer {customer.id}: {e}")
        
        # Sync products
        print("📥 Syncing products...")
        products = stripe.Product.list(limit=100)
        product_count = 0
        for product in products.data:
            try:
                upsert_product(product, conn, cur)
                product_count += 1
            except Exception as e:
                print(f"❌ Error syncing product {product.id}: {e}")
        
        # Sync prices
        print("📥 Syncing prices...")
        prices = stripe.Price.list(limit=100)
        price_count = 0
        for price in prices.data:
            try:
                upsert_price(price, conn, cur)
                price_count += 1
            except Exception as e:
                print(f"❌ Error syncing price {price.id}: {e}")
        
        # Sync subscriptions
        print("📥 Syncing subscriptions...")
        subscriptions = stripe.Subscription.list(limit=100)
        subscription_count = 0
        for subscription in subscriptions.data:
            try:
                upsert_subscription_new(subscription, conn, cur)
                subscription_count += 1
            except Exception as e:
                print(f"❌ Error syncing subscription {subscription.id}: {e}")
        
        conn.commit()
        print(f"\n✅ Sync completed:")
        print(f"   • Customers: {customer_count}")
        print(f"   • Products: {product_count}")
        print(f"   • Prices: {price_count}")
        print(f"   • Subscriptions: {subscription_count}")
        
    except Exception as e:
        conn.rollback()
        print(f"❌ Error syncing Stripe data: {e}")
    finally:
        cur.close()
        conn.close()

def reset_database():
    """Reset database to initial state"""
    print("🔄 Resetting database to initial state...")
    
    # Confirm action
    confirm = input("⚠️  This will delete ALL data and recreate tables. Are you sure? (type 'RESET' to confirm): ")
    if confirm != 'RESET':
        print("❌ Operation cancelled.")
        return
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Read and execute schema file
        schema_file = os.path.join(os.path.dirname(__file__), 'stripe_schema.sql')
        if os.path.exists(schema_file):
            with open(schema_file, 'r') as f:
                schema_sql = f.read()
            
            # Drop all tables first
            cur.execute("DROP SCHEMA public CASCADE;")
            cur.execute("CREATE SCHEMA public;")
            
            # Execute schema
            cur.execute(schema_sql)
            conn.commit()
            print("✅ Database reset completed successfully")
        else:
            print("❌ Schema file not found. Please ensure stripe_schema.sql exists.")
            
    except Exception as e:
        conn.rollback()
        print(f"❌ Error resetting database: {e}")
    finally:
        cur.close()
        conn.close()

def create_admin_user():
    """Create admin user"""
    print("👤 Creating admin user...")
    
    name = input("Enter admin name: ").strip()
    email = input("Enter admin email: ").strip()
    password = input("Enter admin password: ").strip()
    
    if not name or not email or not password:
        print("❌ All fields are required.")
        return
    
    # Import password hashing function
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from app import hash_password
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Check if user exists
        cur.execute("SELECT id FROM app_users WHERE email = %s", (email,))
        if cur.fetchone():
            print("❌ User with this email already exists.")
            return
        
        # Create admin user
        password_hash = hash_password(password)
        cur.execute("""
            INSERT INTO app_users (name, email, password_hash, created_at)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """, (name, email, password_hash, datetime.now()))
        
        user_id = cur.fetchone()['id']
        conn.commit()
        
        print(f"✅ Admin user created successfully with ID: {user_id}")
        
    except Exception as e:
        conn.rollback()
        print(f"❌ Error creating admin user: {e}")
    finally:
        cur.close()
        conn.close()

def backup_data():
    """Backup database data to JSON file"""
    print("💾 Backing up database data...")
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        backup_data = {}
        
        # Get list of tables
        cur.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name;
        """)
        tables = [row['table_name'] for row in cur.fetchall()]
        
        # Backup each table
        for table in tables:
            try:
                cur.execute(f"SELECT * FROM {table}")
                rows = cur.fetchall()
                # Convert to list of dicts for JSON serialization
                backup_data[table] = [dict(row) for row in rows]
                print(f"✅ Backed up table: {table} ({len(rows)} rows)")
            except Exception as e:
                print(f"❌ Error backing up table {table}: {e}")
        
        # Save to file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"backup_{timestamp}.json"
        
        # Custom JSON encoder for datetime objects
        def json_serializer(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
        
        with open(filename, 'w') as f:
            json.dump(backup_data, f, indent=2, default=json_serializer)
        
        print(f"✅ Backup completed: {filename}")
        
    except Exception as e:
        print(f"❌ Error creating backup: {e}")
    finally:
        cur.close()
        conn.close()

def check_app_syntax():
    """Check Flask app for syntax errors"""
    print("🔍 Checking Flask app syntax...")
    
    try:
        import py_compile
        app_file = os.path.join(os.path.dirname(__file__), 'app.py')
        
        if not os.path.exists(app_file):
            print("❌ app.py file not found")
            return
        
        # Check syntax
        py_compile.compile(app_file, doraise=True)
        print("✅ Flask app syntax is valid")
        
        # Try importing the app
        sys.path.append(os.path.dirname(os.path.abspath(__file__)))
        try:
            import app
            print("✅ Flask app imports successfully")
        except Exception as e:
            print(f"❌ Error importing Flask app: {e}")
        
    except py_compile.PyCompileError as e:
        print(f"❌ Syntax error in app.py: {e}")
    except Exception as e:
        print(f"❌ Error checking app syntax: {e}")

def test_database_connection():
    """Test database connection"""
    print("🔗 Testing database connection...")
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Test basic query
        cur.execute("SELECT version();")
        version = cur.fetchone()
        print(f"✅ Database connection successful")
        print(f"   PostgreSQL version: {version[0]}")
        
        # Test app_users table
        try:
            cur.execute("SELECT COUNT(*) FROM app_users;")
            count = cur.fetchone()[0]
            print(f"   app_users table: {count} records")
        except Exception as e:
            print(f"   ⚠️  app_users table issue: {e}")
        
        cur.close()
        conn.close()
        
    except Exception as e:
        print(f"❌ Database connection failed: {e}")

def show_environment():
    """Show environment variables status"""
    print("🌍 Environment Variables:")
    
    required_vars = [
        'PSQL_DB_URL',
        'STRIPE_SECRET_KEY', 
        'STRIPE_PUBLISHABLE_KEY',
        'STRIPE_WEBHOOK_SECRET',
        'FLASK_SECRET_KEY',
        'BASE_URL'
    ]
    
    for var in required_vars:
        value = os.getenv(var)
        if value:
            # Show first 10 chars and last 4 chars for security
            if len(value) > 20:
                masked = value[:10] + "..." + value[-4:]
            else:
                masked = value[:6] + "..."
            print(f"✅ {var}: {masked}")
        else:
            print(f"❌ {var}: Not set")

def show_help():
    """Show help information"""
    print(__doc__)

def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Database Management Utility')
    parser.add_argument('command', help='Command to execute')
    parser.add_argument('args', nargs='*', help='Command arguments')
    
    args = parser.parse_args()
    
    # Command routing
    if args.command == 'list-tables':
        list_tables()
    elif args.command == 'clear-tables':
        clear_all_tables()
    elif args.command == 'clear-table':
        if not args.args:
            print("❌ Table name required. Usage: python manage.py clear-table <table_name>")
            return
        clear_specific_table(args.args[0])
    elif args.command == 'show-stats':
        show_table_stats()
    elif args.command == 'sync-stripe':
        sync_stripe_data()
    elif args.command == 'reset-database':
        reset_database()
    elif args.command == 'create-admin':
        create_admin_user()
    elif args.command == 'backup-data':
        backup_data()
    elif args.command == 'restore-data':
        print("🔄 Restoring data from backup...")
        # TODO: Implement restore data functionality
    elif args.command == 'check-syntax':
        check_app_syntax()
    elif args.command == 'test-db':
        test_database_connection()
    elif args.command == 'show-env':
        show_environment()
    else:
        print(f"❌ Unknown command: {args.command}")
        show_help()

if __name__ == "__main__":
    main()