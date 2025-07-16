import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from datetime import datetime

# Definir o diretório de logs da aplicação
LOG_DIR = "app_logs"
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

LOG_FILE = os.path.join(LOG_DIR, "batman_errors.log")
MAX_BYTES = 5 * 1024 * 1024  # 5 MB
BACKUP_COUNT = 5             # Manter 5 arquivos de backup (Batman.log.1, .2, etc.)

def setup_logging():
    """
    Configura o sistema de logging para a aplicação.
    - Grava logs de nível INFO e acima em um arquivo.
    - Grava logs de nível WARNING e acima no console (para debug em tempo real).
    """
    logger = logging.getLogger("BatmanApp")
    logger.setLevel(logging.INFO) # Nível mínimo para o logger geral

    # Formatter para o log de arquivo
    file_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(name)s - %(message)s'
    )

    # Handler para o arquivo de log com rotação
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding='utf-8'
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # Handler para o console (opcional, útil para debug)
    # Mostra WARNING e acima no console
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.WARNING) # Apenas WARNING e CRITICAL no console
    console_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    logger.info("Sistema de logging configurado.")
    return logger

class StderrRedirector:
    """
    Redireciona sys.stderr para o logger, capturando mensagens de erro não tratadas.
    """
    def __init__(self, logger):
        self.logger = logger
        self.terminal = sys.stderr # Salva o stderr original
        self.buffer = [] # Buffer para acumular mensagens antes de logar

    def write(self, message):
        # Escreve a mensagem no stderr original também, para que o console ainda veja
        if self.terminal:
            self.terminal.write(message)
            self.terminal.flush() # Garante que seja escrito imediatamente

        # Adiciona ao buffer, processa linha por linha para evitar logs incompletos
        self.buffer.append(message)
        if '\n' in message:
            # Se houver uma nova linha, processa o buffer
            full_message = "".join(self.buffer).strip()
            if full_message:
                self.logger.error(f"Captured Stderr: {full_message}")
            self.buffer = [] # Limpa o buffer

    def flush(self):
        # Garante que qualquer coisa no buffer seja logada quando flush() for chamado
        if self.buffer:
            full_message = "".join(self.buffer).strip()
            if full_message:
                self.logger.error(f"Captured Stderr: {full_message}")
            self.buffer = []
        if self.terminal:
            self.terminal.flush()

# Variável global para o logger
app_logger = setup_logging()