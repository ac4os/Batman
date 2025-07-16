import sys
import json
import os
import ctypes
import psutil
import subprocess
import time
import urllib.request # Mantido caso precise para futuras funcionalidades de atualização
import win32serviceutil
import win32service
import win32con
from PyQt5 import QtWidgets, QtGui, QtCore
from datetime import datetime

# Importa as classes do módulo de log e do visualizador
# Assumo que log_viewer.py e app_logger.py estão no mesmo diretório
try:
    from log_viewer import LogViewerDialog
    from app_logger import app_logger, StderrRedirector
except ImportError as e:
    print(f"Erro ao importar módulos de log: {e}. Certifique-se de que 'log_viewer.py' e 'app_logger.py' estão no mesmo diretório.")
    sys.exit(1)


VERSION = "25.7.2"
SERVICOS_FILE = "servicos_cadastrados.json"
# Use a função os.path.join para construir caminhos de forma segura
ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
APP_LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_logs")

SERVICOS_PADRAO = [
    {
        "nome": "srvIntegraWeb",
        "logs": "C:\\Quality\\LOG\\Integra"
    },
    {
        "nome": "ServicoAutomacao",
        "logs": "C:/Quality/LOG/webPostoLeituraAutomacao"
    },
    {
        "nome": "webPostoPayServer",
        "logs": "C:/Quality/LOG/webPostoPayServer"
    },
    {
        "nome": "ServicoFiscal",
        "logs": "C:/Quality/LOG/webPostoFiscalServer"
    }
]

# --- Funções de Utilitários e Gerenciamento de Configuração ---
def is_admin():
    """Verifica se o programa está sendo executado como administrador."""
    try:
        # Tenta verificar se o usuário é administrador
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception as e:
        # Se ocorrer um erro (ex: não é Windows), assume que não é admin
        app_logger.error(f"Erro ao verificar privilégios de admin: {e}")
        return False

def run_as_admin():
    """Reinicia o script com privilégios de administrador."""
    try:
        script = os.path.abspath(sys.argv[0])
        # Constrói os parâmetros garantindo que espaços em argumentos sejam tratados
        params = ' '.join([f'"{arg}"' if ' ' in arg else arg for arg in sys.argv[1:]])
        
        # Log antes de tentar reiniciar
        app_logger.info(f"Tentando reiniciar como administrador: {sys.executable} \"{script}\" {params}")
        
        # ShellExecuteW pode retornar um handle para o novo processo, mas aqui
        # o foco é apenas tentar a execução elevada. O último parâmetro '1' exibe a janela.
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, f'"{script}" {params}', None, 1)
        
        # Se ShellExecuteW retornar sem erro, assume-se que o comando foi enviado.
        # A instância atual deve terminar.
        return True
    except Exception as e:
        app_logger.critical(f"Não foi possível reiniciar o aplicativo com privilégios de administrador: {e}", exc_info=True)
        QtWidgets.QMessageBox.critical(None, "Erro de Permissão",
                                       f"Não foi possível reiniciar o aplicativo com privilégios de administrador.\n"
                                       f"Por favor, execute-o manualmente como administrador.\nErro: {e}")
        return False

def salvar_servicos(lista_servicos):
    """Salva a lista de serviços cadastrados em um arquivo JSON."""
    try:
        with open(SERVICOS_FILE, "w", encoding='utf-8') as f:
            json.dump(lista_servicos, f, indent=4, ensure_ascii=False)
        app_logger.info(f"Serviços salvos em {SERVICOS_FILE}")
    except Exception as e:
        app_logger.error(f"Erro ao salvar serviços em {SERVICOS_FILE}: {e}", exc_info=True)

def carregar_servicos():
    """Carrega a lista de serviços cadastrados do arquivo JSON, ou cria um padrão."""
    if not os.path.isfile(SERVICOS_FILE):
        app_logger.info(f"Arquivo '{SERVICOS_FILE}' não encontrado. Criando serviços padrão.")
        salvar_servicos(SERVICOS_PADRAO)
        return SERVICOS_PADRAO.copy()
    try:
        with open(SERVICOS_FILE, "r", encoding='utf-8') as f:
            dados = json.load(f)
            if not dados:
                app_logger.warning(f"Arquivo '{SERVICOS_FILE}' está vazio. Recriando serviços padrão.")
                salvar_servicos(SERVICOS_PADRAO)
                return SERVICOS_PADRAO.copy()
            app_logger.info(f"Serviços carregados de {SERVICOS_FILE}")
            return dados
    except json.JSONDecodeError as e:
        app_logger.error(f"Erro de JSONDecodeError ao carregar {SERVICOS_FILE}. Recriando serviços padrão. Erro: {e}", exc_info=True)
        salvar_servicos(SERVICOS_PADRAO)
        return SERVICOS_PADRAO.copy()
    except Exception as e:
        app_logger.critical(f"Erro inesperado ao carregar {SERVICOS_FILE}. Recriando serviços padrão. Erro: {e}", exc_info=True)
        salvar_servicos(SERVICOS_PADRAO)
        return SERVICOS_PADRAO.copy()

def listar_servicos_sistema():
    """Lista todos os serviços do Windows (nome interno, nome de exibição)."""
    try:
        hscm = win32service.OpenSCManager(None, None, win32con.GENERIC_READ)
        statuses = win32service.EnumServicesStatus(
            hscm,
            win32service.SERVICE_WIN32,
            win32service.SERVICE_STATE_ALL
        )
        win32service.CloseServiceHandle(hscm)
        app_logger.debug(f"Listados {len(statuses)} serviços do sistema.")
        return [(s[0], s[1]) for s in statuses]
    except Exception as e:
        app_logger.error(f"Erro ao listar serviços do sistema: {e}", exc_info=True)
        return []

def buscar_display_name_por_nome_interno(nome_interno):
    """Retorna o nome de exibição de um serviço a partir de seu nome interno."""
    servicos = listar_servicos_sistema()
    for nome_int, nome_disp in servicos:
        if nome_int.lower() == nome_interno.lower():
            return nome_disp
    app_logger.warning(f"Nome de exibição não encontrado para o serviço interno: '{nome_interno}'.")
    return nome_interno

# --- Funções de Gerenciamento de Serviços Windows ---
def obter_status(servico_nome):
    """Obtém o status de um serviço Windows (Rodando, Parado, Reiniciando, Erro)."""
    try:
        status = win32serviceutil.QueryServiceStatus(servico_nome)[1]
        if status == win32service.SERVICE_RUNNING:
            return "Rodando"
        elif status in (win32service.SERVICE_STOP_PENDING, win32service.SERVICE_START_PENDING,
                        win32service.SERVICE_CONTINUE_PENDING, win32service.SERVICE_PAUSE_PENDING):
            return "Reiniciando"
        else:
            return "Parado"
    except win32service.error as e:
        if e.winerror == 1060:
            app_logger.warning(f"Serviço '{servico_nome}' não existe no sistema (Erro 1060).")
            return "Não Existe"
        app_logger.error(f"Erro ao consultar status do serviço '{servico_nome}': {e}", exc_info=True)
        return "Erro"

def get_pid_servico(servico_nome):
    """Obtém o PID de um serviço Windows."""
    try:
        resultado = subprocess.run(
            ['sc', 'queryex', servico_nome],
            capture_output=True, text=True,
            shell=True,
            creationflags=subprocess.CREATE_NO_WINDOW # Evita que uma janela CMD pisque
        )
        if resultado.returncode != 0:
            app_logger.warning(f"Comando 'sc queryex {servico_nome}' falhou com código {resultado.returncode}. Erro: {resultado.stderr.strip()}")
            return None
        output = resultado.stdout
        for linha in output.splitlines():
            if "PID" in linha:
                try:
                    pid_str = linha.split(":")[1].strip()
                    app_logger.debug(f"PID encontrado para '{servico_nome}': {pid_str}")
                    return int(pid_str)
                except ValueError as ve:
                    app_logger.error(f"Erro ao parsear PID para '{servico_nome}': {ve}. Linha: '{linha}'", exc_info=True)
                    return None
    except Exception as e:
        app_logger.error(f"Erro ao obter PID do serviço '{servico_nome}': {e}", exc_info=True)
    return None

def matar_processo(pid):
    """Mata um processo pelo seu PID."""
    if pid:
        try:
            p = psutil.Process(pid)
            app_logger.info(f"Tentando terminar processo PID {pid}...")
            p.terminate() # Tenta encerrar graciosamente
            p.wait(timeout=5) # Espera por 5 segundos
            if p.is_running():
                app_logger.warning(f"Processo PID {pid} ainda rodando após terminate. Tentando kill...")
                p.kill() # Força o encerramento
                p.wait(timeout=5)
            app_logger.info(f"Processo PID {pid} encerrado com sucesso.")
            return True
        except psutil.NoSuchProcess:
            app_logger.info(f"Processo PID {pid} não existe mais. (Já encerrado)")
            return True
        except Exception as e:
            app_logger.error(f"Erro ao matar processo PID {pid}: {e}", exc_info=True)
            return False
    app_logger.warning("Nenhum PID fornecido para matar processo.")
    return False

def esperar_status(servico_nome, status_esperado, timeout=60): # Increased timeout to 60 seconds
    """Espera por um status específico do serviço até um timeout."""
    inicio = time.time()
    app_logger.info(f"Aguardando status '{status_esperado}' para '{servico_nome}' (timeout: {timeout}s)")
    while time.time() - inicio < timeout:
        status = obter_status(servico_nome)
        if status == status_esperado:
            app_logger.debug(f"Serviço '{servico_nome}' atingiu status '{status_esperado}'.")
            return True
        time.sleep(0.5) # Pequeno delay para evitar consumo excessivo de CPU
    app_logger.warning(f"Timeout: Serviço '{servico_nome}' não atingiu status '{status_esperado}' em {timeout}s. Status atual: {status}")
    return False

def iniciar_servico(servico_nome, progress_callback=None):
    """Inicia um serviço Windows."""
    display_name = buscar_display_name_por_nome_interno(servico_nome)
    app_logger.info(f"Solicitada inicialização do serviço '{display_name}' (internamente: '{servico_nome}')")
    try:
        status = obter_status(servico_nome)
        if status == "Rodando":
            if progress_callback: progress_callback(f"Serviço '{display_name}' já está rodando.", True)
            app_logger.info(f"Serviço '{display_name}' já está rodando.")
            return True
        if status == "Não Existe":
            if progress_callback: progress_callback(f"Erro: Serviço '{display_name}' não existe no sistema.", False)
            app_logger.error(f"Erro: Serviço '{display_name}' não existe no sistema.")
            return False

        if progress_callback: progress_callback(f"Iniciando serviço '{display_name}'...", True)
        win32serviceutil.StartService(servico_nome)
        if esperar_status(servico_nome, "Rodando", timeout=60): # Timeout increased
            if progress_callback: progress_callback(f"Serviço '{display_name}' iniciado com sucesso.", True)
            app_logger.info(f"Serviço '{display_name}' iniciado com sucesso.")
            return True
        else:
            final_status = obter_status(servico_nome)
            if progress_callback: progress_callback(f"Timeout: Serviço '{display_name}' não iniciou. Status atual: {final_status}", False)
            app_logger.error(f"Timeout: Serviço '{display_name}' não iniciou em 60s. Status atual: {final_status}") # Reflecting new timeout
            return False
    except Exception as e:
        if progress_callback: progress_callback(f"Erro ao iniciar '{display_name}': {e}", False)
        app_logger.critical(f"Exceção ao iniciar '{display_name}': {e}", exc_info=True)
        return False

def parar_servico(servico_nome, progress_callback=None):
    """Para um serviço Windows, com opção de matar o processo se não parar normalmente."""
    display_name = buscar_display_name_por_nome_interno(servico_nome)
    app_logger.info(f"Solicitada parada do serviço '{display_name}' (internamente: '{servico_nome}')")
    try:
        status = obter_status(servico_nome)
        if status == "Parado":
            if progress_callback: progress_callback(f"Serviço '{display_name}' já está parado.", True)
            app_logger.info(f"Serviço '{display_name}' já está parado.")
            return True
        if status == "Não Existe":
            if progress_callback: progress_callback(f"Erro: Serviço '{display_name}' não existe no sistema.", False)
            app_logger.error(f"Erro: Serviço '{display_name}' não existe no sistema.")
            return False

        if progress_callback: progress_callback(f"Parando serviço '{display_name}'...", True)
        win32serviceutil.StopService(servico_nome)

        if esperar_status(servico_nome, "Parado", timeout=20):
            if progress_callback: progress_callback(f"Serviço '{display_name}' parado normalmente.", True)
            app_logger.info(f"Serviço '{display_name}' parado normalmente.")
            return True
        else:
            if progress_callback: progress_callback(f"Timeout: Serviço '{display_name}' não parou em 20 segundos. Tentando matar processo...", False)
            app_logger.warning(f"Timeout: Serviço '{display_name}' não parou em 20s. Tentando matar processo.")

        pid = get_pid_servico(servico_nome)
        if pid:
            if matar_processo(pid):
                time.sleep(2) # Give a moment for the process to fully clear
                if obter_status(servico_nome) == "Parado":
                    if progress_callback: progress_callback(f"Processo PID {pid} de '{display_name}' forçosamente encerrado.", True)
                    app_logger.info(f"Processo PID {pid} de '{display_name}' forçosamente encerrado.")
                    return True
                else:
                    if progress_callback: progress_callback(f"Erro: Processo PID {pid} de '{display_name}' não encerrou completamente.", False)
                    app_logger.error(f"Erro: Processo PID {pid} de '{display_name}' não encerrou completamente.")
                    return False
            else:
                if progress_callback: progress_callback(f"Falha ao matar processo PID {pid} de '{display_name}'.", False)
                app_logger.error(f"Falha ao matar processo PID {pid} de '{display_name}'.")
                return False
        else:
            # Se não encontrou PID, verifica novamente o status para garantir que está parado
            if obter_status(servico_nome) == "Parado":
                if progress_callback: progress_callback(f"Serviço '{display_name}' está parado (nenhum PID ativo encontrado).", True)
                app_logger.info(f"Serviço '{display_name}' está parado (nenhum PID ativo encontrado).")
                return True
            else:
                if progress_callback: progress_callback(f"Nenhum processo ativo encontrado para o serviço '{display_name}', mas ainda não está 'Parado'.", False)
                app_logger.warning(f"Nenhum processo ativo encontrado para o serviço '{display_name}', mas ainda não está 'Parado'.")
                return False
    except Exception as e:
        if progress_callback: progress_callback(f"Erro ao parar '{display_name}': {e}", False)
        app_logger.critical(f"Exceção ao parar '{display_name}': {e}", exc_info=True)
        return False

def reiniciar_servico(servico_nome, progress_callback=None):
    """Reinicia um serviço Windows."""
    display_name = buscar_display_name_por_nome_interno(servico_nome)
    app_logger.info(f"Solicitado reinício do serviço '{display_name}' (internamente: '{servico_nome}')")
    try:
        if obter_status(servico_nome) == "Não Existe":
            if progress_callback: progress_callback(f"Erro: Serviço '{display_name}' não existe no sistema.", False)
            app_logger.error(f"Erro: Serviço '{display_name}' não existe no sistema.")
            return False

        if progress_callback: progress_callback(f"Reiniciando serviço '{display_name}'...", True)
        parado = parar_servico(servico_nome, progress_callback)

        if not parado:
            if progress_callback: progress_callback(f"Falha ao reiniciar '{display_name}': Não foi possível parar o serviço.", False)
            app_logger.error(f"Falha ao reiniciar '{display_name}': Não foi possível parar o serviço.")
            return False

        time.sleep(2) # Give a moment between stop and start

        iniciado = iniciar_servico(servico_nome, progress_callback)
        if iniciado:
            if progress_callback: progress_callback(f"Serviço '{display_name}' reiniciado com sucesso!", True)
            app_logger.info(f"Serviço '{display_name}' reiniciado com sucesso!")
            return True
        else:
            if progress_callback: progress_callback(f"Serviço '{display_name}' não reiniciou corretamente.", False)
            app_logger.error(f"Serviço '{display_name}' não reiniciou corretamente.")
            return False
    except Exception as e:
        if progress_callback: progress_callback(f"Erro ao reiniciar '{display_name}': {e}", False)
        app_logger.critical(f"Exceção ao reiniciar '{display_name}': {e}", exc_info=True)
        return False

# --- Classes para Diálogos (Cadastro e Edição) ---
class TelaEdicao(QtWidgets.QWidget): # Mantido como QWidget, pois .show() é usado e não .exec_()
    servico_atualizado = QtCore.pyqtSignal()
    servico_excluido = QtCore.pyqtSignal()

    def __init__(self, servico_original, main_window_callback_status):
        super().__init__()
        self.servico_original = servico_original
        self.main_window_callback_status = main_window_callback_status
        app_logger.info(f"Abrindo tela de edição para serviço: {servico_original['nome']}")

        self.setWindowTitle(f"Batman - Editar Serviço: {buscar_display_name_por_nome_interno(servico_original['nome'])}")
        self.setGeometry(180, 180, 550, 300)
        self.setStyleSheet("""
            QWidget {
                background-color: #202020; /* Fundo escuro */
                color: #e0e0e0; /* Texto cinza claro */
                font-family: 'Segoe UI', 'Roboto', sans-serif;
                font-size: 13px;
                border-radius: 6px; /* Menos arredondado */
            }
            QLabel {
                color: #e0e0e0;
                padding: 3px 0;
            }
            QLineEdit {
                padding: 8px;
                border-radius: 4px; /* Bordas retas */
                border: 1px solid #505050; /* Borda cinza sutil */
                background-color: #2a2a2a; /* Fundo do input mais escuro */
                color: white;
            }
            QPushButton {
                background-color: #3a3a3a; /* Botão cinza escuro */
                color: #ffffff; /* Texto branco */
                padding: 10px 15px;
                border-radius: 4px;
                font-weight: bold;
                border: 1px solid #5a5a5a;
                outline: none;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                border: 1px solid #6a6a6a;
            }
            QPushButton:pressed {
                background-color: #2a2a2a;
                border: 1px solid #7a7a7a;
            }
            QPushButton#btnExcluirEdicao {
                background-color: #a04040; /* Vermelho escuro para delete */
                color: white;
                margin-top: 15px;
                border: 1px solid #c05050;
            }
            QPushButton#btnExcluirEdicao:hover {
                background-color: #b05050;
                border: 1px solid #d06060;
            }
            QPushButton#btnExcluirEdicao:pressed {
                background-color: #903030;
            }
            QLabel {
                color: #d0d0d0; /* Label color for better contrast */
            }
            QLabel > small {
                color: #808080; /* Subtle color for small text */
                font-size: 10px;
            }
        """)

        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        layout.addWidget(QtWidgets.QLabel("Nome de Exibição do Serviço:"))
        self.input_nome_exibicao = QtWidgets.QLineEdit(buscar_display_name_por_nome_interno(self.servico_original['nome']))
        self.input_nome_exibicao.setReadOnly(True)
        layout.addWidget(self.input_nome_exibicao)
        info_label = QtWidgets.QLabel("<small><i>(O nome de exibição não pode ser alterado para serviços do Windows. A edição é para a pasta de logs.)</i></small>")
        info_label.setAlignment(QtCore.Qt.AlignRight)
        info_label.setStyleSheet("color: #7f848e; font-size: 11px;")
        layout.addWidget(info_label)

        layout.addWidget(QtWidgets.QLabel("Pasta Raiz dos Logs do Serviço:"))
        self.input_logs = QtWidgets.QLineEdit(self.servico_original['logs'])
        self.input_logs.setReadOnly(True)

        btn_browse = QtWidgets.QPushButton("Selecionar Nova Pasta")
        btn_browse.clicked.connect(self.selecionar_pasta_logs)

        hlayout = QtWidgets.QHBoxLayout()
        hlayout.addWidget(self.input_logs)
        hlayout.addWidget(btn_browse)
        layout.addLayout(hlayout)

        btn_salvar = QtWidgets.QPushButton("Salvar Alterações")
        btn_salvar.clicked.connect(self.salvar_edicao)
        layout.addWidget(btn_salvar)

        btn_excluir = QtWidgets.QPushButton("❌ Excluir Serviço da Lista")
        btn_excluir.setObjectName("btnExcluirEdicao")
        btn_excluir.clicked.connect(self.excluir_servico)
        layout.addWidget(btn_excluir)

        self.setLayout(layout)

    def selecionar_pasta_logs(self):
        pasta = QtWidgets.QFileDialog.getExistingDirectory(self, "Selecione a nova pasta raiz dos logs")
        if pasta:
            self.input_logs.setText(pasta)
            app_logger.info(f"Nova pasta de logs selecionada: {pasta}")

    def salvar_edicao(self):
        novo_caminho_logs = self.input_logs.text().strip()
        app_logger.info(f"Tentando salvar edição para {self.servico_original['nome']}. Novo caminho: {novo_caminho_logs}")

        if not novo_caminho_logs or not os.path.isdir(novo_caminho_logs):
            self.main_window_callback_status("Erro: Por favor, selecione uma pasta de logs válida.", False)
            QtWidgets.QMessageBox.warning(self, "Erro de Validação", "Por favor, selecione uma pasta de logs válida.")
            app_logger.warning(f"Tentativa de salvar edição com pasta de logs inválida: '{novo_caminho_logs}'")
            return

        cadastrados = carregar_servicos()
        encontrado = False
        for i, s in enumerate(cadastrados):
            if s["nome"] == self.servico_original["nome"]:
                cadastrados[i]["logs"] = novo_caminho_logs
                encontrado = True
                break

        if encontrado:
            salvar_servicos(cadastrados)
            self.main_window_callback_status(f"Serviço '{buscar_display_name_por_nome_interno(self.servico_original['nome'])}' atualizado com sucesso.", True)
            QtWidgets.QMessageBox.information(self, "Sucesso", "Serviço atualizado com sucesso!")
            self.servico_atualizado.emit()
            app_logger.info(f"Serviço '{self.servico_original['nome']}' atualizado com sucesso. Nova pasta de logs: {novo_caminho_logs}")
            self.close()
        else:
            self.main_window_callback_status(f"Erro: Serviço '{buscar_display_name_por_nome_interno(self.servico_original['nome'])}' não encontrado para edição.", False)
            QtWidgets.QMessageBox.warning(self, "Erro", "Serviço não encontrado na lista para atualização.")
            app_logger.error(f"Erro ao salvar edição: Serviço '{self.servico_original['nome']}' não encontrado na lista de serviços.")

    def excluir_servico(self):
        app_logger.info(f"Tentando excluir serviço: {self.servico_original['nome']}")
        resposta = QtWidgets.QMessageBox.question(
            self,
            "Confirmar Exclusão",
            f"Tem certeza que deseja EXCLUIR o serviço '{buscar_display_name_por_nome_interno(self.servico_original['nome'])}' da lista?\n"
            "Esta ação é irreversível.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if resposta == QtWidgets.QMessageBox.Yes:
            cadastrados = carregar_servicos()
            novos_cadastrados = [s for s in cadastrados if s["nome"] != self.servico_original["nome"]]
            salvar_servicos(novos_cadastrados)
            self.main_window_callback_status(f"Serviço '{buscar_display_name_por_nome_interno(self.servico_original['nome'])}' excluído com sucesso.", True)
            QtWidgets.QMessageBox.information(self, "Sucesso", "Serviço excluído da lista!")
            self.servico_excluido.emit()
            app_logger.info(f"Serviço '{self.servico_original['nome']}' excluído da lista.")
            self.close()
        else:
            self.main_window_callback_status(f"Exclusão do serviço '{buscar_display_name_por_nome_interno(self.servico_original['nome'])}' cancelada.", False)
            app_logger.info(f"Exclusão do serviço '{self.servico_original['nome']}' cancelada pelo usuário.")

class WindowsServiceSelectionDialog(QtWidgets.QDialog):
    service_selected = QtCore.pyqtSignal(str) # Emits the internal service name

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Selecionar Serviço Windows")
        self.setGeometry(300, 300, 600, 400)
        self.setWindowModality(QtCore.Qt.WindowModal)

        self.setStyleSheet("""
            QDialog {
                background-color: #202020; /* Fundo escuro */
                color: #e0e0e0;
                font-family: 'Segoe UI', 'Roboto', sans-serif;
                font-size: 13px;
                border-radius: 6px;
            }
            QLabel {
                color: #e0e0e0;
                padding: 3px 0;
            }
            QListWidget {
                background-color: #2a2a2a; /* Fundo da lista mais escuro */
                border: 1px solid #404040; /* Borda mais discreta */
                border-radius: 4px;
                color: #e0e0e0;
                padding: 4px; /* Menor padding */
            }
            QListWidget::item {
                padding: 6px; /* Padding ajustado */
            }
            QListWidget::item:selected {
                background-color: #50505a; /* Seleção sutil de cinza azulado */
                color: white;
            }
            QLineEdit {
                padding: 8px;
                border-radius: 4px;
                border: 1px solid #505050;
                background-color: #2a2a2a;
                color: white;
            }
            QPushButton {
                background-color: #3a3a3a;
                color: #ffffff;
                padding: 9px 14px; /* Padding ajustado */
                border-radius: 4px;
                font-weight: bold;
                border: 1px solid #5a5a5a;
                outline: none;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                border: 1px solid #6a6a6a;
            }
            QPushButton:pressed {
                background-color: #2a2a2a;
                border: 1px solid #7a7a7a;
            }
        """)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        layout.addWidget(QtWidgets.QLabel("Digite para filtrar ou selecione um serviço:"))
        self.filter_input = QtWidgets.QLineEdit()
        self.filter_input.setPlaceholderText("Filtrar serviços...")
        self.filter_input.textChanged.connect(self.filter_services)
        layout.addWidget(self.filter_input)

        self.service_list_widget = QtWidgets.QListWidget()
        self.service_list_widget.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.service_list_widget)

        buttons_layout = QtWidgets.QHBoxLayout()
        self.select_button = QtWidgets.QPushButton("Selecionar")
        self.select_button.clicked.connect(self.accept)
        cancel_button = QtWidgets.QPushButton("Cancelar")
        cancel_button.clicked.connect(self.reject)

        buttons_layout.addStretch()
        buttons_layout.addWidget(self.select_button)
        buttons_layout.addWidget(cancel_button)
        layout.addLayout(buttons_layout)

        self.load_services()

    def load_services(self):
        self.all_services = listar_servicos_sistema()
        self.display_services(self.all_services)
        app_logger.info(f"Carregados {len(self.all_services)} serviços do sistema para seleção.")

    def display_services(self, services_to_display):
        self.service_list_widget.clear()
        for internal_name, display_name in services_to_display:
            item = QtWidgets.QListWidgetItem(f"{display_name} ({internal_name})")
            item.setData(QtCore.Qt.UserRole, internal_name) # Store internal name in item data
            self.service_list_widget.addItem(item)

    def filter_services(self, text):
        filtered_services = []
        lower_text = text.lower()
        for internal_name, display_name in self.all_services:
            if lower_text in internal_name.lower() or lower_text in display_name.lower():
                filtered_services.append((internal_name, display_name))
        self.display_services(filtered_services)

    def accept(self):
        selected_item = self.service_list_widget.currentItem()
        if selected_item:
            internal_name = selected_item.data(QtCore.Qt.UserRole)
            self.service_selected.emit(internal_name)
            app_logger.info(f"Serviço selecionado na caixa de diálogo: {internal_name}")
            super().accept()
        else:
            QtWidgets.QMessageBox.warning(self, "Nenhum Serviço Selecionado", "Por favor, selecione um serviço da lista.")

class TelaCadastro(QtWidgets.QDialog): # Changed from QWidget to QDialog
    servico_adicionado = QtCore.pyqtSignal()

    def __init__(self, main_window_update_callback, main_window_status_callback):
        super().__init__()
        self.main_window_update_callback = main_window_update_callback
        self.main_window_status_callback = main_window_status_callback
        app_logger.info("Abrindo tela de cadastro de novo serviço.")

        self.setWindowTitle("Batman - Adicionar Novo Serviço")
        self.setGeometry(250, 250, 480, 300) # Increased height for new button
        self.setWindowModality(QtCore.Qt.ApplicationModal) # Make it modal
        self.setStyleSheet("""
            QWidget {
                background-color: #202020; /* Fundo escuro */
                color: #e0e0e0;
                font-family: 'Segoe UI', 'Roboto', sans-serif;
                font-size: 13px;
                border-radius: 6px;
            }
            QLabel {
                color: #e0e0e0;
                padding: 3px 0;
            }
            QLineEdit {
                padding: 8px;
                border-radius: 4px;
                border: 1px solid #505050;
                background-color: #2a2a2a;
                color: white;
            }
            QPushButton {
                background-color: #3a3a3a;
                color: #ffffff;
                padding: 10px 15px;
                border-radius: 4px;
                font-weight: bold;
                border: 1px solid #5a5a5a;
                outline: none;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                border: 1px solid #6a6a6a;
            }
            QPushButton:pressed {
                background-color: #2a2a2a;
                border: 1px solid #7a7a7a;
            }
        """)

        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        layout.addWidget(QtWidgets.QLabel("Nome Interno do Serviço Windows:"))
        self.input_nome = QtWidgets.QLineEdit()
        self.input_nome.setPlaceholderText("Ex: srvIntegraWeb")
        btn_browse_windows_services = QtWidgets.QPushButton("Selecionar Serviço do Windows")
        btn_browse_windows_services.clicked.connect(self.selecionar_servico_windows)
        hlayout_nome = QtWidgets.QHBoxLayout()
        hlayout_nome.addWidget(self.input_nome)
        hlayout_nome.addWidget(btn_browse_windows_services)
        layout.addLayout(hlayout_nome)


        layout.addWidget(QtWidgets.QLabel("Pasta Raiz dos Logs (Ex: C:\\Quality\\LOG\\Integra):"))
        self.input_logs = QtWidgets.QLineEdit()
        self.input_logs.setPlaceholderText("Ex: C:\\Quality\\LOG\\Integra")
        self.input_logs.setReadOnly(True)
        btn_browse_logs = QtWidgets.QPushButton("Selecionar Pasta de Logs")
        btn_browse_logs.clicked.connect(self.selecionar_pasta_logs)
        hlayout_logs = QtWidgets.QHBoxLayout()
        hlayout_logs.addWidget(self.input_logs)
        hlayout_logs.addWidget(btn_browse_logs)
        layout.addLayout(hlayout_logs)

        btn_salvar = QtWidgets.QPushButton("Salvar Serviço")
        btn_salvar.clicked.connect(self.salvar_servico)
        layout.addWidget(btn_salvar)

        self.setLayout(layout)

    def selecionar_servico_windows(self):
        dialog = WindowsServiceSelectionDialog(self)
        dialog.service_selected.connect(self.input_nome.setText)
        dialog.exec_()

    def selecionar_pasta_logs(self):
        pasta = QtWidgets.QFileDialog.getExistingDirectory(self, "Selecione a pasta raiz dos logs")
        if pasta:
            self.input_logs.setText(pasta)
            app_logger.info(f"Pasta de logs selecionada na tela de cadastro: {pasta}")

    def salvar_servico(self):
        nome_servico = self.input_nome.text().strip()
        pasta_logs = self.input_logs.text().strip()
        app_logger.info(f"Tentando cadastrar novo serviço: '{nome_servico}' com pasta '{pasta_logs}'")

        if not nome_servico:
            self.main_window_status_callback("Erro: O nome do serviço não pode ser vazio.", False)
            QtWidgets.QMessageBox.warning(self, "Erro de Validação", "Por favor, insira o nome do serviço.")
            app_logger.warning("Tentativa de salvar serviço com nome vazio.")
            return

        # Validate if the service exists in Windows Services
        system_services = [s[0].lower() for s in listar_servicos_sistema()]
        if nome_servico.lower() not in system_services:
            self.main_window_status_callback(f"Erro: O serviço '{nome_servico}' não foi encontrado no Windows.", False)
            QtWidgets.QMessageBox.warning(self, "Erro de Validação", f"O serviço com o nome interno '{nome_servico}' não foi encontrado nos serviços do Windows.\n"
                                         "Por favor, verifique o nome ou selecione um serviço existente.")
            app_logger.warning(f"Tentativa de cadastrar serviço '{nome_servico}' que não existe no Windows.")
            return

        if not pasta_logs or not os.path.isdir(pasta_logs):
            self.main_window_status_callback("Erro: Por favor, selecione uma pasta de logs válida.", False)
            QtWidgets.QMessageBox.warning(self, "Erro de Validação", "Por favor, selecione uma pasta de logs válida e existente.")
            app_logger.warning(f"Tentativa de salvar serviço com pasta de logs inválida/não existente: '{pasta_logs}'")
            return

        cadastrados = carregar_servicos()
        for s in cadastrados:
            if s["nome"].lower() == nome_servico.lower():
                self.main_window_status_callback(f"Erro: Serviço '{nome_servico}' já cadastrado.", False)
                QtWidgets.QMessageBox.warning(self, "Erro", "Este serviço já está cadastrado.")
                app_logger.warning(f"Tentativa de cadastrar serviço duplicado: '{nome_servico}'")
                return

        novo_servico = {"nome": nome_servico, "logs": pasta_logs}
        cadastrados.append(novo_servico)
        salvar_servicos(cadastrados)
        self.main_window_status_callback(f"Serviço '{buscar_display_name_por_nome_interno(nome_servico)}' adicionado com sucesso!", True)
        QtWidgets.QMessageBox.information(self, "Sucesso", "Serviço adicionado com sucesso!")
        self.servico_adicionado.emit()
        app_logger.info(f"Novo serviço '{nome_servico}' adicionado com sucesso.")
        self.accept() # Use accept() to close the QDialog

# --- Classe para o Diálogo de Progresso ---
class ProgressDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, title="Processando...", message="Aguarde..."):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(QtCore.Qt.Dialog | QtCore.Qt.CustomizeWindowHint | QtCore.Qt.WindowTitleHint)
        self.setFixedSize(400, 150)
        self.setWindowModality(QtCore.Qt.WindowModal)
        self.setStyleSheet("""
            QDialog {
                background-color: #202020;
                color: #e0e0e0;
                font-family: 'Segoe UI', 'Roboto', sans-serif;
                font-size: 14px;
                border-radius: 6px;
            }
            QLabel#progressMessageLabel {
                font-size: 16px;
                font-weight: bold;
                color: #b0b0b0; /* Mais sutil */
                padding: 15px;
                text-align: center;
            }
            QProgressBar {
                height: 12px; /* Ligeiramente mais alta */
                border: 1px solid #505050; /* Borda da barra de progresso */
                border-radius: 5px;
                background-color: #303030; /* Fundo da barra de progresso */
                text-align: center;
                color: white; /* Cor do texto de porcentagem */
            }
            QProgressBar::chunk {
                background-color: #4a90d9; /* Cor do preenchimento da barra de progresso */
                border-radius: 5px;
            }
        """)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        self.progress_label = QtWidgets.QLabel(message)
        self.progress_label.setObjectName("progressMessageLabel")
        self.progress_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.progress_label)

        self.progress_bar = QtWidgets.QProgressBar(self)
        self.progress_bar.setRange(0, 0) # Modo indeterminado
        layout.addWidget(self.progress_bar)

        self.center_on_parent()

    def set_message(self, message, is_success=True):
        self.progress_label.setText(message)
        # O sucesso/falha aqui pode ser usado para mudar a cor do texto da label se desejado
        # Ex: self.progress_label.setStyleSheet("color: green;" if is_success else "color: red;")
        self.repaint() # Força a atualização da UI

    def center_on_parent(self):
        if self.parent():
            parent_rect = self.parent().frameGeometry()
            self_rect = self.frameGeometry()
            self_rect.moveCenter(parent_rect.center())
            self.move(self_rect.topLeft())

# --- Classes de Threads (Workers) ---

class WorkerSignals(QtCore.QObject):
    finished = QtCore.pyqtSignal()
    error = QtCore.pyqtSignal(str) # Para erros gerais do worker
    progress_message = QtCore.pyqtSignal(str, bool) # Mensagem de progresso e se é sucesso (True/False)
    worker_completed = QtCore.pyqtSignal() # Sinal para indicar que um worker completou sua tarefa (e pode ter um resultado final)
    result = QtCore.pyqtSignal(str, str) # Sinal para o resultado do status do serviço

class ServiceStatusWorker(QtCore.QRunnable):
    """Worker para obter o status de um serviço em background."""
    def __init__(self, service_name):
        super().__init__()
        self.service_name = service_name
        self.signals = WorkerSignals()

    @QtCore.pyqtSlot()
    def run(self):
        try:
            status = obter_status(self.service_name)
            self.signals.result.emit(status, "") # Emite o status e uma string vazia para erro
        except Exception as e:
            error_msg = f"Erro ao obter status de '{self.service_name}': {e}"
            app_logger.error(error_msg)
            self.signals.error.emit(error_msg)
        finally:
            self.signals.finished.emit()

class ServiceWorker(QtCore.QRunnable):
    """Worker para executar ações (iniciar, parar, reiniciar) em um serviço em background."""
    def __init__(self, service_name, action):
        super().__init__()
        self.service_name = service_name
        self.action = action
        self.signals = WorkerSignals()

    @QtCore.pyqtSlot()
    def run(self):
        success = False
        message = ""
        try:
            if self.action == "iniciar":
                self.signals.progress_message.emit(f"Iniciando serviço '{buscar_display_name_por_nome_interno(self.service_name)}'...", True)
                success = iniciar_servico(self.service_name, self.signals.progress_message.emit)
                message = f"Serviço '{buscar_display_name_por_nome_interno(self.service_name)}' {'iniciado' if success else 'falha ao iniciar'}."
            elif self.action == "parar":
                self.signals.progress_message.emit(f"Parando serviço '{buscar_display_name_por_nome_interno(self.service_name)}'...", True)
                success = parar_servico(self.service_name, self.signals.progress_message.emit)
                message = f"Serviço '{buscar_display_name_por_nome_interno(self.service_name)}' {'parado' if success else 'falha ao parar'}."
            elif self.action == "reiniciar":
                self.signals.progress_message.emit(f"Reiniciando serviço '{buscar_display_name_por_nome_interno(self.service_name)}'...", True)
                success = reiniciar_servico(self.service_name, self.signals.progress_message.emit)
                message = f"Serviço '{buscar_display_name_por_nome_interno(self.service_name)}' {'reiniciado' if success else 'falha ao reiniciar'}."
            else:
                message = f"Ação desconhecida: {self.action}"
                success = False

            # Garante que a mensagem final seja emitida
            self.signals.progress_message.emit(message, success)
            app_logger.info(f"Serviço '{self.service_name}' {self.action} - Resultado: {message} (Sucesso: {success})")
            
        except Exception as e:
            message = f"Erro inesperado ao {self.action} o serviço '{buscar_display_name_por_nome_interno(self.service_name)}': {e}"
            app_logger.critical(message)
            self.signals.progress_message.emit(message, False)
            self.signals.error.emit(message)
        finally:
            self.signals.finished.emit()
            self.signals.worker_completed.emit() # Sinal para indicar que esta ação específica do worker completou


class BulkActionWorker(QtCore.QRunnable):
    """Worker para executar ações em massa (iniciar/parar todos) em background."""
    def __init__(self, servicos, action):
        super().__init__()
        self.servicos = servicos
        self.action = action
        self.signals = WorkerSignals()
        self.success_count = 0
        self.fail_count = 0

    @QtCore.pyqtSlot()
    def run(self):
        app_logger.info(f"Iniciando ação em massa: {self.action} todos os serviços.")
        total_servicos = len(self.servicos)
        self.signals.progress_message.emit(f"Iniciando {self.action} todos os serviços...", True)

        for i, servico in enumerate(self.servicos):
            service_name = servico["nome"]
            display_name = buscar_display_name_por_nome_interno(service_name)
            
            self.signals.progress_message.emit(f"({i+1}/{total_servicos}) {self.action.capitalize()}do '{display_name}'...", True)
            
            success = False
            message = ""
            if self.action == "iniciar":
                success = iniciar_servico(service_name, self.signals.progress_message.emit)
                message = f"Serviço '{display_name}' {'iniciado' if success else 'falha ao iniciar'}."
            elif self.action == "parar":
                success = parar_servico(service_name, self.signals.progress_message.emit)
                message = f"Serviço '{display_name}' {'parado' if success else 'falha ao parar'}."
            
            if success:
                self.success_count += 1
                app_logger.info(f"Ação em massa: '{display_name}' {self.action}da com sucesso.")
            else:
                self.fail_count += 1
                app_logger.error(f"Ação em massa: Falha ao {self.action} '{display_name}': {message}")

            self.signals.progress_message.emit(f"({i+1}/{total_servicos}) '{display_name}': {message}", success)
            time.sleep(0.5) # Pequeno atraso para feedback visual

        final_message = f"Ação em massa '{self.action}' concluída. Sucessos: {self.success_count}, Falhas: {self.fail_count}."
        app_logger.info(final_message)
        self.signals.progress_message.emit(final_message, self.fail_count == 0)
        self.signals.finished.emit()


# --- Widget de Serviço Individual ---
class ServicoWidget(QtWidgets.QFrame):
    def __init__(self, servico_data, main_window_callback_status, thread_pool, main_window_instance, parent=None):
        super().__init__(parent)
        self.servico = servico_data
        self.main_window_callback_status = main_window_callback_status
        self.thread_pool = thread_pool
        self.main_window = main_window_instance # Armazena a referência direta para a MainWindow
        self.setObjectName("ServicoWidget") # Para estilização via CSS
        
        # Obter o display_name real se não estiver definido na configuração
        self.display_name = buscar_display_name_por_nome_interno(self.servico["nome"])
        
        self.setStyleSheet("""
            QFrame#ServicoWidget {
                border: 1px solid #444; /* Borda cinza escura */
                border-radius: 8px; /* Cantos arredondados */
                background-color: #2b2b2b; /* Fundo mais escuro para o widget */
                margin: 5px; /* Espaçamento entre os widgets */
            }
            QFrame#ServicoWidget:hover {
                background-color: #3a3a3a; /* Mudar cor ao passar o mouse */
            }
            QLabel#NomeServicoLabel {
                font-weight: bold;
                font-size: 16px;
                color: #f0f0f0; /* Branco para o nome do serviço */
            }
            QLabel#StatusServicoLabel {
                font-weight: bold;
                font-size: 14px;
                color: #fff; /* Cor padrão, será alterada via código */
            }
            QPushButton {
                background-color: #4a4a4a; /* Cor de botão mais escura */
                color: white;
                border: 1px solid #5a5a5a;
                border-radius: 5px;
                padding: 8px 12px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #5a5a5a;
            }
            QPushButton:pressed {
                background-color: #3a3a3a;
            }
            QPushButton#btnLog {
                background-color: #2a6d8f; /* Azul para o botão de log */
            }
            QPushButton#btnLog:hover {
                background-color: #3a7da0;
            }
            QPushButton#btnLog:pressed {
                background-color: #1a5d7f;
            }
            QPushButton#btnEditar {
                background-color: #8c7320; /* Amarelo/laranja para editar */
            }
            QPushButton#btnEditar:hover {
                background-color: #9c8330;
            }
            QPushButton#btnEditar:pressed {
                background-color: #7c6310;
            }
        """)

        self.init_ui()
        self.atualizar_status_ui("Aguardando...") # Status inicial

    def init_ui(self):
        main_layout = QtWidgets.QHBoxLayout(self)
        main_layout.setContentsMargins(15, 10, 15, 10)
        main_layout.setSpacing(15)

        # Coluna de Informações do Serviço
        info_layout = QtWidgets.QVBoxLayout()
        self.lbl_nome_servico = QtWidgets.QLabel(self.display_name)
        self.lbl_nome_servico.setObjectName("NomeServicoLabel")
        info_layout.addWidget(self.lbl_nome_servico)

        self.lbl_status = QtWidgets.QLabel("Status: Carregando...")
        self.lbl_status.setObjectName("StatusServicoLabel")
        info_layout.addWidget(self.lbl_status)
        main_layout.addLayout(info_layout)

        main_layout.addStretch(1) # Empurra tudo para os lados

        # Coluna de Botões
        buttons_layout = QtWidgets.QHBoxLayout()
        buttons_layout.setSpacing(10)

        self.btn_iniciar = QtWidgets.QPushButton("Iniciar")
        self.btn_iniciar.clicked.connect(lambda: self.executar_acao("iniciar"))
        buttons_layout.addWidget(self.btn_iniciar)

        self.btn_parar = QtWidgets.QPushButton("Parar")
        self.btn_parar.clicked.connect(lambda: self.executar_acao("parar"))
        buttons_layout.addWidget(self.btn_parar)

        self.btn_reiniciar = QtWidgets.QPushButton("Reiniciar")
        self.btn_reiniciar.clicked.connect(lambda: self.executar_acao("reiniciar"))
        buttons_layout.addWidget(self.btn_reiniciar)

        self.btn_log = QtWidgets.QPushButton("Log")
        self.btn_log.setObjectName("btnLog")
        self.btn_log.clicked.connect(self.abrir_log_viewer)
        buttons_layout.addWidget(self.btn_log)
        
        self.btn_editar = QtWidgets.QPushButton("Editar")
        self.btn_editar.setObjectName("btnEditar")
        self.btn_editar.clicked.connect(self.abrir_tela_edicao)
        buttons_layout.addWidget(self.btn_editar)

        main_layout.addLayout(buttons_layout)

        self.atualizar_status_background() # Atualiza o status ao criar o widget

    def atualizar_status_ui(self, status):
        """Atualiza o label de status e sua cor na UI."""
        self.lbl_status.setText(f"Status: {status}")
        
        if status == "Rodando":
            self.lbl_status.setStyleSheet("color: #4CAF50;") # Verde
            self.btn_iniciar.setEnabled(False)
            self.btn_parar.setEnabled(True)
            self.btn_reiniciar.setEnabled(True)
            self.btn_log.setEnabled(True)
        elif status == "Parado":
            self.lbl_status.setStyleSheet("color: #F44336;") # Vermelho
            self.btn_iniciar.setEnabled(True)
            self.btn_parar.setEnabled(False)
            self.btn_reiniciar.setEnabled(True)
            self.btn_log.setEnabled(True)
        elif status == "Reiniciando":
            self.lbl_status.setStyleSheet("color: #FFC107;") # Amarelo/Laranja
            self.btn_iniciar.setEnabled(False)
            self.btn_parar.setEnabled(False)
            self.btn_reiniciar.setEnabled(False)
            self.btn_log.setEnabled(False)
        elif status == "Não Existe":
            self.lbl_status.setStyleSheet("color: #9E9E9E;") # Cinza
            self.btn_iniciar.setEnabled(False)
            self.btn_parar.setEnabled(False)
            self.btn_reiniciar.setEnabled(False)
            self.btn_log.setEnabled(False)
            self.lbl_nome_servico.setText(f"{self.display_name} (Não Existe)")
        elif status == "Erro":
            self.lbl_status.setStyleSheet("color: #D32F2F;") # Vermelho mais escuro para erro
            self.btn_iniciar.setEnabled(False)
            self.btn_parar.setEnabled(False)
            self.btn_reiniciar.setEnabled(False)
            self.btn_log.setEnabled(False)
        else: # Qualquer outro status (ex: "Aguardando...")
            self.lbl_status.setStyleSheet("color: #8C8C8C;") # Cinza escuro
            self.btn_iniciar.setEnabled(False)
            self.btn_parar.setEnabled(False)
            self.btn_reiniciar.setEnabled(False)
            self.btn_log.setEnabled(False)

    def atualizar_status_background(self):
        """Atualiza o status do serviço em uma thread separada."""
        worker = ServiceStatusWorker(self.servico["nome"])
        worker.signals.result.connect(lambda status, _: self.atualizar_status_ui(status))
        worker.signals.error.connect(lambda msg: self.main_window_callback_status(msg, False))
        self.thread_pool.start(worker)

    def executar_acao(self, acao):
        """Executa uma ação no serviço (iniciar, parar, reiniciar) em uma thread separada."""
        self.main_window_callback_status(f"Solicitando {acao} para '{self.display_name}'...", True)
        self.atualizar_status_ui("Reiniciando") # Define um status provisório enquanto a ação ocorre

        worker = ServiceWorker(self.servico["nome"], acao)
        worker.signals.progress_message.connect(self.main_window_callback_status)
        worker.signals.finished.connect(self.atualizar_status_background) # Atualiza o status final após a ação
        worker.signals.error.connect(lambda msg: self.main_window_callback_status(msg, False))
        self.thread_pool.start(worker)

    def abrir_log_viewer(self):
        """Abre o visualizador de logs para o serviço."""
        log_path = self.servico.get("logs")
        if not log_path or not os.path.isdir(log_path):
            QtWidgets.QMessageBox.warning(self, "Caminho de Log Inválido",
                                          f"A pasta de logs para '{self.display_name}' não está configurada ou não existe:\n{log_path}")
            self.main_window_callback_status(f"Erro: Pasta de log inválida para '{self.display_name}'.", False)
            app_logger.warning(f"Tentativa de abrir log com caminho inválido para '{self.servico['nome']}': {log_path}")
            return

        app_logger.info(f"Abrindo visualizador de logs para '{self.servico['nome']}' em: {log_path}")
        self.main_window_callback_status(f"Abrindo visualizador de logs para '{self.display_name}'...", True)
        
        self.log_viewer_dialog = LogViewerDialog(log_path, self)
        self.log_viewer_dialog.show() # Usar show() para não bloquear a janela principal

    def abrir_tela_edicao(self):
        """Abre a tela de edição para o serviço atual."""
        self.main_window_callback_status(f"Abrindo tela de edição para '{self.display_name}'...", True)
        self.tela_edicao = TelaEdicao(self.servico, self.main_window_callback_status)
        self.tela_edicao.servico_atualizado.connect(self.on_servico_editado)
        self.tela_edicao.servico_excluido.connect(self.on_servico_excluido)
        self.tela_edicao.show()

    def on_servico_editado(self):
        """Callback quando o serviço é editado na TelaEdicao."""
        # Usa a referência direta à MainWindow
        if self.main_window:
            self.main_window.servicos = carregar_servicos()
            self.main_window.carregar_servicos_na_ui() # Recarrega todos os widgets para refletir a mudança
            app_logger.info(f"Serviço '{self.display_name}' editado. UI recarregada.")
            self.main_window_callback_status(f"Serviço '{self.display_name}' atualizado e UI recarregada.", True)
        else:
            app_logger.error(f"Não foi possível encontrar a MainWindow para recarregar serviços após edição de '{self.display_name}'.")

    def on_servico_excluido(self):
        """Callback quando o serviço é excluído na TelaEdicao."""
        # Usa a referência direta à MainWindow
        if self.main_window:
            self.main_window.servicos = carregar_servicos()
            self.main_window.carregar_servicos_na_ui() # Recarrega todos os widgets para refletir a remoção
            app_logger.info(f"Serviço '{self.display_name}' excluído. UI recarregada.")
            self.main_window_callback_status(f"Serviço '{self.display_name}' excluído e UI recarregada.", True)
        else:
            app_logger.error(f"Não foi possível encontrar a MainWindow para recarregar serviços após exclusão de '{self.display_name}'.")

# --- Janela Principal ---
class MainWindow(QtWidgets.QMainWindow):
    """Janela principal da aplicação Batman."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Batman - Gerenciador de Serviços v{VERSION}")
        self.setGeometry(100, 100, 800, 600)
        
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QtGui.QIcon(ICON_PATH))

        self.servicos = carregar_servicos()
        self.thread_pool = QtCore.QThreadPool()
        self.thread_pool.setMaxThreadCount(16) # Define um número razoável de threads
        app_logger.info(f"Thread pool inicializado com {self.thread_pool.maxThreadCount()} threads.")

        self.init_ui()
        app_logger.info("UI principal configurada.")
        self.carregar_servicos_na_ui()
        app_logger.info("Carregando serviços na UI.")
        self.iniciar_timer_atualizacao_status()
        
        # Carregar configurações da janela
        self.load_window_settings()

        # Configurar hook para fechar a aplicação
        self.app_closing = False
        app.aboutToQuit.connect(self.on_app_quit)

    def init_ui(self):
        self.central_widget = QtWidgets.QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QtWidgets.QVBoxLayout(self.central_widget)

        # Barra de menu
        self.create_menu_bar()

        # Barra de Status
        self.status_bar = self.statusBar()
        self.status_bar.showMessage(f"Bem-vindo ao Batman v{VERSION}!")

        # Área de Rolagem para Serviços
        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content_widget = QtWidgets.QWidget()
        self.services_layout = QtWidgets.QVBoxLayout(self.scroll_content_widget)
        self.services_layout.addStretch(1) # Para que os widgets fiquem no topo
        self.scroll_area.setWidget(self.scroll_content_widget)
        self.main_layout.addWidget(self.scroll_area)

        # Botões de Ação em Massa
        self.bulk_actions_layout = QtWidgets.QHBoxLayout()
        self.btn_iniciar_todos = QtWidgets.QPushButton("Iniciar Todos")
        # Ícones podem ser carregados de arquivos se não houver temas disponíveis
        self.btn_iniciar_todos.setIcon(QtGui.QIcon(os.path.join(os.path.dirname(__file__), "icons", "start.png"))) 
        self.btn_iniciar_todos.clicked.connect(lambda: self.executar_acao_em_massa("iniciar"))
        self.bulk_actions_layout.addWidget(self.btn_iniciar_todos)

        self.btn_parar_todos = QtWidgets.QPushButton("Parar Todos")
        self.btn_parar_todos.setIcon(QtGui.QIcon(os.path.join(os.path.dirname(__file__), "icons", "stop.png")))
        self.btn_parar_todos.clicked.connect(lambda: self.executar_acao_em_massa("parar"))
        self.bulk_actions_layout.addWidget(self.btn_parar_todos)

        self.btn_atualizar_todos = QtWidgets.QPushButton("Atualizar Todos os Status")
        self.btn_atualizar_todos.setIcon(QtGui.QIcon(os.path.join(os.path.dirname(__file__), "icons", "refresh.png")))
        self.btn_atualizar_todos.clicked.connect(self.atualizar_todos_os_servicos_ui)
        self.bulk_actions_layout.addWidget(self.btn_atualizar_todos)
        
        self.bulk_actions_layout.addStretch(1) # Empurra os botões para a esquerda
        self.main_layout.addLayout(self.bulk_actions_layout)

    def adicionar_servico_a_ui(self, servico_data):
        """Adiciona um ServicoWidget para o serviço na interface."""
        # Passa a referência da própria MainWindow para o ServicoWidget
        servico_widget = ServicoWidget(servico_data, self.exibir_status_na_barra, self.thread_pool, main_window_instance=self)
        self.services_layout.addWidget(servico_widget)
        app_logger.info(f"Serviço '{servico_data['nome']}' adicionado à UI.")

    def create_menu_bar(self):
        menubar = self.menuBar()

        # Menu Arquivo
        file_menu = menubar.addMenu("&Arquivo")
        
        exit_action = QtWidgets.QAction("Sair", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.setStatusTip("Sair da aplicação")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Menu Serviços
        services_menu = menubar.addMenu("&Serviços")

        add_service_action = QtWidgets.QAction("Adicionar Serviço", self)
        add_service_action.setStatusTip("Adicionar um novo serviço à lista")
        add_service_action.triggered.connect(self.dialog_adicionar_servico)
        services_menu.addAction(add_service_action)

        services_menu.addSeparator()

        # Menu Ferramentas
        tools_menu = menubar.addMenu("&Ferramentas")

        open_logs_folder_action = QtWidgets.QAction("Abrir Pasta de Logs da Aplicação", self)
        open_logs_folder_action.setStatusTip("Abre a pasta onde os logs da aplicação são salvos")
        open_logs_folder_action.triggered.connect(self.abrir_pasta_logs_app)
        tools_menu.addAction(open_logs_folder_action)

        # Menu Ajuda
        help_menu = menubar.addMenu("&Ajuda")
        about_action = QtWidgets.QAction("Sobre", self)
        about_action.setStatusTip("Sobre esta aplicação")
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)

    def exibir_status_na_barra(self, mensagem, is_success=True):
        """Exibe uma mensagem na barra de status."""
        # Se a aplicação estiver fechando, não tenta atualizar a barra de status
        if self.app_closing:
            return

        self.status_bar.showMessage(mensagem)
        if is_success:
            self.status_bar.setStyleSheet("QStatusBar {color: green;}")
        else:
            self.status_bar.setStyleSheet("QStatusBar {color: red;}")
        app_logger.info(f"Status UI: {mensagem}")

    def carregar_servicos_na_ui(self):
        """Carrega os serviços configurados na interface do usuário."""
        # Limpa os widgets existentes antes de adicionar novos
        while self.services_layout.count() > 0:
            item = self.services_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            
        app_logger.info(f"Serviços carregados de {SERVICOS_FILE}")
        self.servicos = carregar_servicos() # Esta linha garante que self.servicos é atualizado
        # app_logger.debug(f"Serviços carregados na UI: {self.servicos}") # Linha de depuração, pode ser mantida ou removida
        for servico in self.servicos:
            self.adicionar_servico_a_ui(servico)
        self.services_layout.addStretch(1) # Garante que os itens fiquem no topo

    def dialog_adicionar_servico(self):
        """Abre um diálogo para adicionar um novo serviço."""
        dialog = TelaCadastro(self.carregar_servicos_na_ui, self.exibir_status_na_barra)
        dialog.servico_adicionado.connect(self.carregar_servicos_na_ui)
        dialog.exec_() # Executa como modal
        # Força um redesenho ou atualização após o diálogo fechar e os serviços serem recarregados
        self.scroll_area.update()
        self.central_widget.update()

    def executar_acao_em_massa(self, action):
        """Executa uma ação (iniciar/parar) em todos os serviços."""
        if not self.servicos:
            self.exibir_status_na_barra(f"Nenhum serviço configurado para {action}.", False)
            return

        dialog_title = f"{action.capitalize()} Todos os Serviços"
        dialog_message = f"Por favor, aguarde enquanto todos os serviços estão sendo {action}dos..."

        progress_dialog = ProgressDialog(self, dialog_title, dialog_message)
        progress_dialog.show()

        worker = BulkActionWorker(self.servicos, action)
        worker.signals.progress_message.connect(progress_dialog.set_message)
        worker.signals.progress_message.connect(self.exibir_status_na_barra) # Atualiza a barra de status principal
        worker.signals.finished.connect(progress_dialog.close)
        worker.signals.finished.connect(self.atualizar_todos_os_servicos_ui) # Atualiza a UI após a ação em massa
        self.thread_pool.start(worker)

    def atualizar_todos_os_servicos_ui(self):
        """Solicita que cada ServicoWidget atualize seu status na UI."""
        app_logger.info("Status UI: Atualizando status de todos os serviços...")
        self.exibir_status_na_barra("Atualizando status de todos os serviços...", True)
        for i in range(self.services_layout.count()):
            widget_item = self.services_layout.itemAt(i)
            if widget_item and widget_item.widget() and isinstance(widget_item.widget(), ServicoWidget):
                widget_item.widget().atualizar_status_background()
        self.exibir_status_na_barra("Atualização de status de todos os serviços concluída.", True)

    def iniciar_timer_atualizacao_status(self):
        """Inicia um timer para atualizar o status de todos os serviços periodicamente."""
        self.status_update_timer = QtCore.QTimer(self)
        self.status_update_timer.setInterval(30000) # 30 segundos
        self.status_update_timer.timeout.connect(self.atualizar_todos_os_servicos_ui)
        self.status_update_timer.start()
        app_logger.info("Timer de atualização de status iniciado.")

    def abrir_pasta_logs_app(self):
        """Abre a pasta onde os logs da aplicação são salvos."""
        # Usa a variável global APP_LOGS_DIR
        if not os.path.exists(APP_LOGS_DIR):
            os.makedirs(APP_LOGS_DIR)
        try:
            subprocess.Popen(['explorer', os.path.realpath(APP_LOGS_DIR)])
            self.exibir_status_na_barra("Pasta de logs da aplicação aberta.", True)
        except Exception as e:
            app_logger.error(f"Erro ao abrir pasta de logs da aplicação: {e}")
            QtWidgets.QMessageBox.warning(self, "Erro", "Não foi possível abrir a pasta de logs da aplicação.")
            self.exibir_status_na_barra("Erro ao abrir pasta de logs da aplicação.", False)

    def show_about_dialog(self):
        """Exibe o diálogo 'Sobre'."""
        about_text = f"""
        <html>
        <h3>Batman - Gerenciador de Serviços</h3>
        <p>Versão: {VERSION}</p>
        <p>Desenvolvido por: TrinLabs</p>
        <p>Um utilitário para gerenciar serviços do Windows de forma fácil.</p>
        <p>Entre em contato para suporte ou feedback.</p>
        </html>
        """
        QtWidgets.QMessageBox.about(self, "Sobre o Batman", about_text)

    def load_window_settings(self):
        """Carrega as configurações de posição e tamanho da janela."""
        settings = QtCore.QSettings("TrinLabs", "Batman")
        if settings.contains("geometry"):
            self.restoreGeometry(settings.value("geometry"))
            app_logger.info("Configurações de geometria da janela carregadas.")
        else:
            app_logger.info("Configurações de geometria da janela não encontradas. Usando padrão.")
        if settings.contains("windowState"):
            self.restoreState(settings.value("windowState"))
            app_logger.info("Configurações de estado da janela carregadas.")
        else:
            app_logger.info("Configurações de estado da janela não encontradas. Usando padrão.")

    def save_window_settings(self):
        """Salva as configurações de posição e tamanho da janela."""
        settings = QtCore.QSettings("TrinLabs", "Batman")
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("windowState", self.saveState())
        app_logger.info("Configurações da janela salvas.")

    def closeEvent(self, event):
        """Manipulador de evento de fechamento da janela."""
        app_logger.info("MainWindow fechando. Salvando configurações da janela.")
        self.save_window_settings()
        
        # Parar o timer de atualização de status
        if self.status_update_timer.isActive():
            self.status_update_timer.stop()
            app_logger.info("Timer de atualização de status parado.")

        # Define a flag para indicar que a aplicação está fechando
        self.app_closing = True 
        
        event.accept()

    def on_app_quit(self):
        """Manipulador de evento quando a aplicação está prestes a sair."""
        # Removido app.exitCode() para evitar o AttributeError no fechamento.
        app_logger.info("Aplicação encerrada.")


# --- Configuração de Exceções PyQt ---
def qt_exception_hook(exc_type, exc_value, exc_traceback):
    """
    Hook para capturar exceções PyQt não tratadas e logá-las.
    Mostra uma QMessageBox para o usuário.
    """
    app_logger.critical("Exceção PyQt não tratada!", exc_info=(exc_type, exc_value, exc_traceback))
    
    # Cria uma caixa de mensagem para informar o usuário sobre o erro.
    error_dialog = QtWidgets.QMessageBox()
    error_dialog.setIcon(QtWidgets.QMessageBox.Critical)
    error_dialog.setWindowTitle("Erro Crítico da Aplicação")
    error_dialog.setText("Ocorreu um erro inesperado e a aplicação será encerrada.")
    error_dialog.setInformativeText("Detalhes do erro foram registrados nos logs da aplicação. Por favor, reporte este erro para ajudar na solução do problema.")
    # Garante que o diretório de logs exista antes de referenciá-lo no erro detalhado
    if not os.path.exists(APP_LOGS_DIR):
        os.makedirs(APP_LOGS_DIR)
    log_file_path = os.path.join(APP_LOGS_DIR, "Batman.log") # Assumindo o nome do arquivo de log
    error_dialog.setDetailedText(f"Tipo de Erro: {exc_type.__name__}\n"
                                 f"Mensagem: {exc_value}\n"
                                 f"Detalhes foram gravados em '{log_file_path}'.")
    error_dialog.setStandardButtons(QtWidgets.QMessageBox.Ok)
    error_dialog.exec_()
    sys.exit(1) # Encerra o aplicativo
    

# --- Execução Principal ---
def main():
    """Função principal para iniciar a aplicação."""
    # Redirecionar stderr para o nosso logger ANTES de criar o QApplication
    # Isso garante que mesmo erros na inicialização da GUI sejam capturados.
    sys.stderr = StderrRedirector(app_logger)
    app_logger.info("Application started.")

    # Define o hook de exceção para PyQt antes de criar o QApplication
    # para capturar erros na GUI.
    sys.excepthook = qt_exception_hook

    global app
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Batman")
    app.setOrganizationName("TrinLabs")

    if not is_admin():
        app_logger.warning("Aplicação não está rodando como administrador. Tentando reiniciar...")
        if run_as_admin():
            # Se run_as_admin TRUE significa que a tentativa de reiniciar foi feita.
            # A instância atual (não admin) deve SAIR IMEDIATAMENTE para evitar o loop.
            app_logger.info("Tentativa de reinício como administrador enviada. Encerrando instância não-admin.")
            sys.exit(0)
        else:
            # Se run_as_admin retornar False, significa que a elevação falhou
            # ou o usuário cancelou. Neste caso, o programa não pode continuar
            # e deve sair.
            app_logger.critical("Falha ao obter privilégios de administrador ou usuário cancelou. Saindo.")
            sys.exit(1)

    # Se chegou até aqui, significa que a aplicação está rodando como administrador
    # (ou já estava desde o início).
    janela = MainWindow()
    janela.show()
    app_logger.info("MainWindow exibida. Entrando no loop de eventos.")
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()