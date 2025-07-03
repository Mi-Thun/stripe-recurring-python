"""
Comprehensive logging configuration for Stripe Integration Server
"""
import os
import logging
import logging.handlers
from datetime import datetime


class StripeIntegrationLoggerSetup:
    """Centralized logging configuration for Stripe integration"""
    
    def __init__(self, log_level=logging.INFO):
        self.log_level = log_level
        self.logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
        self.setup_directories()
        
    def setup_directories(self):
        """Create necessary log directories"""
        os.makedirs(self.logs_dir, exist_ok=True)
        
    def get_formatter(self, detailed=True):
        """Get logging formatter"""
        if detailed:
            return logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
            )
        else:
            return logging.Formatter(
                '%(asctime)s - %(levelname)s - %(message)s'
            )
    
    def setup_main_logger(self, name='stripe_integration'):
        """Setup main application logger"""
        logger = logging.getLogger(name)
        logger.setLevel(self.log_level)
        
        # Remove existing handlers
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(self.get_formatter(detailed=False))
        logger.addHandler(console_handler)
        
        # Main log file
        file_handler = logging.handlers.RotatingFileHandler(
            os.path.join(self.logs_dir, 'stripe_integration.log'),
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(self.get_formatter(detailed=True))
        logger.addHandler(file_handler)
        
        # Error log file
        error_handler = logging.handlers.RotatingFileHandler(
            os.path.join(self.logs_dir, 'errors.log'),
            maxBytes=5*1024*1024,  # 5MB
            backupCount=5
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(self.get_formatter(detailed=True))
        logger.addHandler(error_handler)
        
        return logger
    
    def setup_database_logger(self):
        """Setup specialized database operations logger"""
        logger = logging.getLogger('database')
        logger.setLevel(logging.DEBUG)
        
        # Remove existing handlers
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            
        # Database operations log file
        db_handler = logging.handlers.RotatingFileHandler(
            os.path.join(self.logs_dir, 'database_operations.log'),
            maxBytes=5*1024*1024,  # 5MB
            backupCount=5
        )
        db_handler.setLevel(logging.DEBUG)
        db_handler.setFormatter(self.get_formatter(detailed=True))
        logger.addHandler(db_handler)
        
        return logger
    
    def suppress_external_loggers(self):
        """Suppress verbose external library loggers"""
        logging.getLogger('stripe').setLevel(logging.WARNING)
        logging.getLogger('psycopg2').setLevel(logging.WARNING)
        logging.getLogger('urllib3').setLevel(logging.WARNING)
        logging.getLogger('requests').setLevel(logging.WARNING)
        logging.getLogger('werkzeug').setLevel(logging.WARNING)
    
    def setup_all_loggers(self):
        """Setup all loggers for the application"""
        self.suppress_external_loggers()
        
        main_logger = self.setup_main_logger()
        db_logger = self.setup_database_logger()
        
        # Log initialization
        main_logger.info("="*60)
        main_logger.info("Stripe Integration Server - Logging Initialized")
        main_logger.info(f"Timestamp: {datetime.now().isoformat()}")
        main_logger.info(f"Log Level: {logging.getLevelName(self.log_level)}")
        main_logger.info(f"Logs Directory: {self.logs_dir}")
        main_logger.info("="*60)
        
        return {
            'main': main_logger,
            'database': db_logger,

        }


def log_system_info(logger):
    """Log system and environment information"""
    import platform
    import sys
    
    logger.info("System Information:")
    logger.info(f"  Python Version: {sys.version}")
    logger.info(f"  Platform: {platform.platform()}")
    logger.info(f"  Architecture: {platform.architecture()}")
    logger.info(f"  Processor: {platform.processor()}")
    
    logger.info("Environment Variables:")
    sensitive_vars = ['STRIPE_SECRET_KEY', 'STRIPE_WEBHOOK_SECRET', 'PSQL_DB_URL']
    for var in ['BASE_URL'] + sensitive_vars:
        value = os.getenv(var, 'Not Set')
        if var in sensitive_vars and value != 'Not Set':
            value = '***REDACTED***'
        logger.info(f"  {var}: {value}")


def log_startup_banner(logger):
    """Log application startup banner"""
    banner = """
    ┌─────────────────────────────────────────────────────────────┐
    │          🔄 STRIPE INTEGRATION SERVER STARTING 🔄           │ 
    └─────────────────────────────────────────────────────────────┘
    """
    for line in banner.split('\n'):
        if line.strip():
            logger.info(line)


class LoggerContext:
    """Context manager for request-scoped logging"""
    
    def __init__(self, logger, request_id, operation_name):
        self.logger = logger
        self.request_id = request_id
        self.operation_name = operation_name
        self.start_time = None
        
    def __enter__(self):
        self.start_time = datetime.now()
        self.logger.info(f"[{self.request_id}] Starting {self.operation_name}")
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        end_time = datetime.now()
        duration = (end_time - self.start_time).total_seconds()
        
        if exc_type is None:
            self.logger.info(f"[{self.request_id}] Completed {self.operation_name} in {duration:.3f}s")
        else:
            self.logger.error(f"[{self.request_id}] Failed {self.operation_name} after {duration:.3f}s: {exc_val}")
        
    def log(self, level, message):
        """Log a message with request context"""
        getattr(self.logger, level)(f"[{self.request_id}] {message}")
