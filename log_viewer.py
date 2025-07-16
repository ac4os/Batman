# log_viewer.py

import os
from PyQt5 import QtWidgets, QtGui, QtCore
from datetime import datetime
import json
import logging
import stat

# Configuração básica do logger para o módulo (opcional, pode ser centralizado)
app_logger = logging.getLogger(__name__)
if not app_logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('[%(name)s %(levelname)s] %(asctime)s - %(message)s')
    handler.setFormatter(formatter)
    app_logger.addHandler(handler)
    app_logger.setLevel(logging.DEBUG)


# Importe as novas classes
# Certifique-se de que log_highlighter.py e highlight_settings_dialog.py estão no mesmo diretório
from log_highlighter import LogHighlighter
from highlight_settings_dialog import HighlightSettingsDialog

class LogFileReader(QtCore.QObject):
    """
    Worker para ler e monitorar um arquivo de log em uma thread separada.
    Emite novas linhas em lotes para otimizar a atualização da UI.
    """
    new_log_lines = QtCore.pyqtSignal(list)
    filtered_full_log = QtCore.pyqtSignal(list)
    error_occurred = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()
    file_loaded = QtCore.pyqtSignal() # Sinal para indicar que um novo arquivo foi carregado

    def __init__(self):
        super().__init__()
        self.log_file_path = None
        self.watcher = QtCore.QFileSystemWatcher()
        self.file_handle = None
        self.is_running = True
        self.current_position = 0
        self.line_buffer = [] # Buffer para linhas que ainda não foram enviadas à UI

        self._all_log_lines = []

        self._filter_term = ""
        self._filter_mode = "include"

        self.buffer_timer = QtCore.QTimer()
        self.buffer_timer.setInterval(50)
        self.buffer_timer.timeout.connect(self._flush_buffer)

        self.polling_timer = QtCore.QTimer()
        self.polling_timer.setInterval(1000)
        self.polling_timer.timeout.connect(self._read_new_lines_if_needed)

        self._debug_mode = True # Manter para logs internos do FileReader

        self.watcher.fileChanged.connect(self._on_file_changed_signal)
        app_logger.debug("[LogFileReader] Inicializado.")


    def _log_debug(self, message):
        if self._debug_mode:
            app_logger.debug(f"[LogFileReader] {message}")

    def set_filter(self, term, mode):
        self._filter_term = term.lower() if term else ""
        self._filter_mode = mode
        self._log_debug(f"Filtro atualizado: Termo='{self._filter_term}', Modo='{self._filter_mode}'. Reaplicando filtro no log completo.")
        # Reenvia o log completo filtrado para atualizar a UI
        self._send_filtered_full_log()

    def _should_line_be_visible(self, line):
        if not self._filter_term:
            return True

        line_lower = line.lower()
        if self._filter_mode == "include":
            return self._filter_term in line_lower
        elif self._filter_mode == "exclude":
            return self._filter_term not in line_lower
        return True


    def set_log_file(self, new_path):
        """Define e começa a monitorar um novo arquivo de log."""
        self._log_debug(f"Chamado set_log_file para: {new_path}")
        self.stop_monitoring() # Para o monitoramento atual, se houver

        # Remove o caminho antigo do watcher, se ele estiver lá
        if self.log_file_path and self.log_file_path in self.watcher.files():
            self.watcher.removePath(self.log_file_path)
            self._log_debug(f"Removido path '{self.log_file_path}' do watcher.")

        self.log_file_path = new_path
        self.current_position = 0
        self.line_buffer = []
        self._all_log_lines = []
        # Emite file_loaded ANTES de start_monitoring para que a UI possa se redefinir
        self.file_loaded.emit()
        self.start_monitoring()
        self._log_debug(f"Caminho do log atualizado e monitoramento iniciado para: {new_path}")


    def start_monitoring(self):
        """Inicia o monitoramento do arquivo de log atual."""
        if not self.log_file_path:
            self._log_debug("Não há arquivo de log definido para monitorar.")
            return

        self._log_debug(f"Iniciando monitoramento para: {self.log_file_path}")
        self.is_running = True
        try:
            if not os.path.exists(self.log_file_path):
                error_msg = f"Arquivo de log não encontrado: {self.log_file_path}"
                self.error_occurred.emit(error_msg)
                self._log_debug(f"ERRO: {error_msg}")
                self.is_running = False
                self.finished.emit()
                return
            
            # Verificar se é um arquivo e não um diretório
            if not os.path.isfile(self.log_file_path):
                error_msg = f"Caminho especificado '{self.log_file_path}' não é um arquivo. É um diretório ou outro tipo de entrada."
                self.error_occurred.emit(error_msg)
                self._log_debug(f"ERRO: {error_msg}")
                self.is_running = False
                self.finished.emit()
                return


            if self.log_file_path not in self.watcher.files():
                self.watcher.addPath(self.log_file_path)
                self._log_debug(f"Adicionado path '{self.log_file_path}' ao watcher.")
            else:
                self._log_debug(f"Path '{self.log_file_path}' já está no watcher.")


            if self.file_handle:
                self.file_handle.close()
                self._log_debug(f"Handle do arquivo antigo fechado.")

            try:
                # Tenta abrir o arquivo. Se for um diretório aqui dará PermissionError ou IsADirectoryError
                self.file_handle = open(self.log_file_path, 'r', encoding='utf-8', errors='ignore')
            except Exception as e:
                error_msg = f"Falha ao abrir o arquivo de log '{self.log_file_path}': {e}. Verifique permissões."
                self.error_occurred.emit(error_msg)
                self._log_debug(f"ERRO: {error_msg}")
                self.is_running = False
                self.finished.emit()
                return

            self.file_handle.seek(0)
            self.current_position = self.file_handle.tell()
            self._log_debug(f"Arquivo '{os.path.basename(self.log_file_path)}' aberto. Posição inicial: {self.current_position}")


            self._all_log_lines.append(f"--- Monitorando log: {os.path.basename(self.log_file_path)} ---")
            self._all_log_lines.append(f"--- Data/Hora Início: {QtCore.QDateTime.currentDateTime().toString('yyyy-MM-dd HH:mm:ss')} ---")

            self._read_initial_lines()

            self.buffer_timer.start()
            self.polling_timer.start()
            self._log_debug(f"Monitoramento iniciado com sucesso para {self.log_file_path}.")

        except PermissionError as e:
            error_msg = f"Erro de permissão ao abrir arquivo de log: {e}. Verifique se o arquivo está sendo usado por outro programa ou se o caminho é um diretório."
            self.error_occurred.emit(error_msg)
            self._log_debug(f"ERRO de Permissão: {error_msg}")
            self.is_running = False
            self.finished.emit()
        except Exception as e:
            error_msg = f"Erro inesperado ao iniciar monitoramento: {e}"
            self.error_occurred.emit(error_msg)
            self._log_debug(f"ERRO: {e}")
            self.is_running = False
            self.finished.emit()

    def _add_line_to_all_log_and_buffer(self, line):
        self._all_log_lines.append(line)
        if self._should_line_be_visible(line):
            self.line_buffer.append(line)

    def _flush_buffer(self):
        if self.line_buffer and self.is_running:
            self.new_log_lines.emit(self.line_buffer)
            self.line_buffer = []
            self._log_debug(f"Buffer de novas linhas emitido. Buffer agora vazio.")


    def _send_filtered_full_log(self):
        filtered_lines = [line for line in self._all_log_lines if self._should_line_be_visible(line)]
        self._log_debug(f"Enviando {len(filtered_lines)} linhas (log completo filtrado) para a UI.")
        self.filtered_full_log.emit(filtered_lines)


    def _read_initial_lines(self, num_lines=1000):
        """Lê as últimas N linhas do arquivo de log na inicialização."""
        self._log_debug(f"Lendo {num_lines} linhas iniciais...")
        if not self.file_handle:
            self._log_debug("file_handle é None, não é possível ler linhas iniciais.")
            return

        try:
            # Ir para o final para pegar o tamanho
            self.file_handle.seek(0, os.SEEK_END)
            file_size = self.file_handle.tell()
            self._log_debug(f"Tamanho do arquivo para leitura inicial: {file_size} bytes.")

            # Estimativa de bytes para ler as últimas N linhas (média de 200 bytes por linha)
            read_bytes_from_end = num_lines * 200
            start_position = max(0, file_size - read_bytes_from_end)

            self._log_debug(f"Buscando para a posição inicial de leitura: {start_position}")
            self.file_handle.seek(start_position)

            lines = self.file_handle.readlines()
            self._log_debug(f"Lidas {len(lines)} linhas a partir de {start_position}.")

            # Se começamos no meio do arquivo, a primeira linha pode estar incompleta
            if start_position > 0 and len(lines) > 0:
                self._log_debug("Descartando a primeira linha lida (potencialmente parcial).")
                lines = lines[1:]

            # Pega apenas as últimas 'num_lines' linhas
            lines_to_add = lines[-num_lines:] if len(lines) > num_lines else lines
            self._log_debug(f"Adicionando {len(lines_to_add)} linhas iniciais ao _all_log_lines.")

            for line in lines_to_add:
                self._all_log_lines.append(line.strip())

            # Envia o log completo (com as linhas iniciais) para a UI
            self._send_filtered_full_log()

            # Posiciona o handle do arquivo no final para monitorar novas linhas
            self.file_handle.seek(0, os.SEEK_END)
            self.current_position = self.file_handle.tell()
            self._log_debug(f"Posição atualizada após leitura inicial (no final do arquivo): {self.current_position}")

            self._all_log_lines.append("\n--- Fim das linhas iniciais. Monitorando novas entradas ---")
            self._send_filtered_full_log()

        except Exception as e:
            error_msg = f"Erro ao ler linhas iniciais do log: {e}"
            self.error_occurred.emit(error_msg)
            self._log_debug(f"ERRO: {error_msg}")
            self.stop_monitoring()


    def stop_monitoring(self):
        """Para o monitoramento do arquivo de log."""
        if not self.is_running:
            self._log_debug("Monitoramento já parado.")
            return

        self._log_debug(f"Parando monitoramento para: {self.log_file_path}")
        self.is_running = False
        self.buffer_timer.stop()
        self.polling_timer.stop()
        self._flush_buffer() # Garante que as linhas pendentes sejam enviadas
        if self.file_handle:
            self.file_handle.close()
            self.file_handle = None

        if self.log_file_path and self.log_file_path in self.watcher.files():
            self.watcher.removePath(self.log_file_path)
            self._log_debug(f"Removido path '{self.log_file_path}' do watcher ao parar.")

        self.finished.emit()
        self._log_debug(f"Monitoramento parado.")


    def _on_file_changed_signal(self, path):
        """Slot para o sinal fileChanged do QFileSystemWatcher."""
        if path == self.log_file_path:
            self._log_debug(f"Sinal 'fileChanged' disparado para: {path}")
            self._read_new_lines()
        else:
            self._log_debug(f"Sinal 'fileChanged' para arquivo não monitorado atualmente: {path}")

    def _read_new_lines_if_needed(self):
        """Verifica se há novas linhas usando polling e as lê."""
        if not self.is_running or not self.log_file_path:
            return

        try:
            current_file_size = os.path.getsize(self.log_file_path)
            if current_file_size > self.current_position:
                self._log_debug(f"Polling detectou novas linhas. Tamanho atual: {current_file_size}, Posição: {self.current_position}")
                self._read_new_lines()
            elif current_file_size < self.current_position:
                 # Caso o arquivo tenha sido truncado ou resetado pelo programa de log
                self._log_debug(f"Arquivo truncado detectado via polling! ({current_file_size} < {self.current_position}).")
                self._handle_file_truncation()
        except FileNotFoundError:
            self._log_debug(f"Arquivo não encontrado durante polling: {self.log_file_path}")
            self.error_occurred.emit(f"Arquivo monitorado '{os.path.basename(self.log_file_path)}' foi movido ou excluído.")
            self.stop_monitoring()
        except Exception as e:
            self._log_debug(f"Polling error checking file size: {e}")


    def _handle_file_truncation(self):
        """Trata o caso em que o arquivo de log é truncado/resetado."""
        self._log_debug(f"Arquivo truncado detectado! Reiniciando leitura de {self.log_file_path}")
        self._all_log_lines.append("\n--- Arquivo de log resetado/truncado. Reiniciando leitura. ---")
        self._send_filtered_full_log() # Envia a mensagem de reset para a UI

        if self.file_handle:
            self.file_handle.close()
            self.file_handle = None # Fechar e reabrir para garantir nova leitura do início

        try:
            # Reabre o arquivo e reinicia a posição
            self.file_handle = open(self.log_file_path, 'r', encoding='utf-8', errors='ignore')
            self.file_handle.seek(0)
            self.current_position = 0
            self._all_log_lines = [] # Limpa todas as linhas antigas
            self._read_initial_lines() # Lê as novas linhas iniciais do arquivo resetado
        except Exception as e:
            error_msg = f"Erro ao reabrir arquivo truncado '{self.log_file_path}': {e}"
            self.error_occurred.emit(error_msg)
            self._log_debug(f"ERRO: {error_msg}")
            self.stop_monitoring()


    def _read_new_lines(self):
        """Lê as novas linhas adicionadas ao arquivo."""
        if not self.is_running or not self.file_handle:
            self._log_debug("Não está rodando ou handle é None, não lendo novas linhas.")
            return

        try:
            current_file_size = os.path.getsize(self.log_file_path)
            self._log_debug(f"Tamanho atual do arquivo: {current_file_size} bytes. Posição anterior: {self.current_position} bytes.")

            if current_file_size < self.current_position:
                # Isso já é tratado por _handle_file_truncation via polling ou fileChanged
                self._log_debug(f"Detectado truncamento de arquivo durante read_new_lines (já deve ser tratado pelo polling/watcher).")
                self.stop_monitoring() # Pode ser um estado inconsistente, melhor parar
                return

            self.file_handle.seek(self.current_position)
            new_data = self.file_handle.read()
            self.current_position = self.file_handle.tell()

            if new_data:
                lines = new_data.splitlines()
                self._log_debug(f"Lidas {len(lines)} novas linhas. Nova posição: {self.current_position} bytes.")
                for line in lines:
                    if line.strip(): # Adiciona apenas linhas não vazias
                        self._add_line_to_all_log_and_buffer(line.strip()) # strip() para remover espaços em branco

        except PermissionError as e:
            error_msg = f"Erro de permissão ao ler novas linhas: {e}. Verifique se o arquivo está sendo usado por outro programa."
            self.error_occurred.emit(error_msg)
            self._log_debug(f"ERRO de Permissão: {e}")
            # Não para o monitoramento imediatamente, pois pode ser um problema temporário
        except Exception as e:
            error_msg = f"Erro inesperado ao ler novas linhas: {e}"
            self.error_occurred.emit(error_msg)
            self._log_debug(f"ERRO: {e}")


class LogViewerDialog(QtWidgets.QDialog):
    def __init__(self, log_directory_path, parent=None):
        super().__init__(parent)
        self.initial_log_directory = log_directory_path
        self.current_log_file_path = None
        self.setWindowTitle(f"WebBatman - Visualizador de Log")

        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowMaximizeButtonHint | QtCore.Qt.WindowMinimizeButtonHint)
        self.setGeometry(100, 100, 1200, 800)

        self.auto_scroll_enabled = True
        self.is_user_scrolling = False
        self._current_font_size = 10

        self._search_term = ""
        self._search_case_sensitive = False
        self._last_found_cursor = None

        self._filter_term = ""
        self._filter_mode = "include"

        self._highlight_rules = []

        self.setStyleSheet("""
            QDialog {
                background-color: #2E3440;
                color: #ECEFF4;
                font-family: 'Inter', 'Segoe UI', 'Roboto', sans-serif;
                font-size: 14px;
            }
            QTextEdit {
                background-color: #3B4252;
                color: #ECEFF4;
                border: 1px solid #4C566A;
                border-radius: 5px;
                padding: 10px;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 13px;
            }
            QListWidget {
                background-color: #3B4252;
                color: #ECEFF4;
                border: 1px solid #4C566A;
                border-radius: 5px;
                padding: 5px;
            }
            QListWidget::item {
                padding: 3px;
            }
            QListWidget::item:selected {
                background-color: #81A1C1;
                color: #2E3440;
            }
            QPushButton {
                background-color: #88C0D0;
                color: #2E3440;
                padding: 8px 15px;
                border-radius: 5px;
                font-weight: bold;
                border: none;
            }
            QPushButton:hover {
                background-color: #81A1C1;
            }
            QPushButton:checked {
                background-color: #A3BE8C;
                color: #2E3440;
            }
            QScrollBar:vertical {
                border: none;
                background: #3B4252;
                width: 12px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: #81A1C1;
                min-height: 20px;
                border-radius: 6px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                border: none;
                background: none;
            }
            #searchBar, #filterBar {
                background-color: #4C566A;
                padding: 5px;
                border-radius: 5px;
            }
            QLineEdit {
                background-color: #3B4252;
                color: #ECEFF4;
                border: 1px solid #4C566A;
                border-radius: 3px;
                padding: 3px;
            }
            QCheckBox {
                color: #ECEFF4;
            }
            QComboBox {
                background-color: #3B4252;
                color: #ECEFF4;
                border: 1px solid #4C566A;
                border-radius: 3px;
                padding: 3px;
            }
            QComboBox::drop-down {
                border: 0px;
            }
            QComboBox::down-arrow {
                image: url(data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAcAAAAECAYAAADtTMIDAAAAAXNSR0IArs4c6QAAADFJREFUCJljYGBgeP/PwMDAwMDAwLAzMDYg/g/E/xkYGNgMDIwMDCAKgwMDAwMDgwEAS7MFQvQc9j0AAAAASUVORK5CYII=); /* Placeholder para seta */
            }
        """)

        main_layout = QtWidgets.QVBoxLayout(self)

        # Usar QSplitter para redimensionar painéis
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        main_layout.addWidget(splitter)

        # --- Painel Esquerdo: Lista de Arquivos de Log ---
        left_panel_widget = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_panel_widget)
        left_layout.addWidget(QtWidgets.QLabel("Arquivos de Log:"))
        self.file_filter_input = QtWidgets.QLineEdit()
        self.file_filter_input.setPlaceholderText("Filtrar arquivos...")
        self.file_filter_input.textChanged.connect(self._filter_log_files)
        left_layout.addWidget(self.file_filter_input)

        self.file_list_widget = QtWidgets.QListWidget()
        self.file_list_widget.itemClicked.connect(self._on_log_file_selected)
        left_layout.addWidget(self.file_list_widget)

        btn_refresh_files = QtWidgets.QPushButton("Atualizar Lista")
        btn_refresh_files.clicked.connect(self._load_log_files_from_directory)
        left_layout.addWidget(btn_refresh_files)

        splitter.addWidget(left_panel_widget)


        # --- Painel Direito: Conteúdo do Log e Controles ---
        right_panel_widget = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_panel_widget)

        # Barra de Pesquisa
        search_layout = QtWidgets.QHBoxLayout()
        search_layout.setObjectName("searchBar")
        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("Pesquisar...")
        self.search_input.textChanged.connect(self._reset_search)
        self.search_input.returnPressed.connect(lambda: self._find_text(QtGui.QTextDocument.FindNext))
        search_layout.addWidget(self.search_input)

        self.search_case_sensitive_checkbox = QtWidgets.QCheckBox("Aa")
        self.search_case_sensitive_checkbox.setToolTip("Sensível a Maiúsculas/Minúsculas")
        self.search_case_sensitive_checkbox.stateChanged.connect(self._reset_search)
        search_layout.addWidget(self.search_case_sensitive_checkbox)

        self.find_prev_button = QtWidgets.QPushButton("▲")
        self.find_prev_button.setToolTip("Encontrar Anterior")
        self.find_prev_button.clicked.connect(lambda: self._find_text(QtGui.QTextDocument.FindBackward))
        search_layout.addWidget(self.find_prev_button)

        self.find_next_button = QtWidgets.QPushButton("▼")
        self.find_next_button.setToolTip("Encontrar Próximo")
        self.find_next_button.clicked.connect(lambda: self._find_text(QtGui.QTextDocument.FindNext))
        search_layout.addWidget(self.find_next_button)

        right_layout.addLayout(search_layout)

        # Barra de Filtro
        filter_layout = QtWidgets.QHBoxLayout()
        filter_layout.setObjectName("filterBar")
        self.filter_input = QtWidgets.QLineEdit()
        self.filter_input.setPlaceholderText("Filtrar linhas...")
        self.filter_input.textChanged.connect(self._apply_filter)
        filter_layout.addWidget(self.filter_input)

        self.filter_mode_combo = QtWidgets.QComboBox()
        self.filter_mode_combo.addItem("Incluir", "include")
        self.filter_mode_combo.addItem("Excluir", "exclude")
        self.filter_mode_combo.currentIndexChanged.connect(self._apply_filter)
        filter_layout.addWidget(self.filter_mode_combo)

        right_layout.addLayout(filter_layout)

        self.log_text_edit = QtWidgets.QTextEdit()
        self.log_text_edit.setReadOnly(True)
        self.log_text_edit.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
        self.log_text_edit.setFontPointSize(self._current_font_size)
        self.log_text_edit.document().setMaximumBlockCount(20000)
        right_layout.addWidget(self.log_text_edit)

        self.highlighter = LogHighlighter(self.log_text_edit.document())
        self._load_custom_highlight_rules()
        self.highlighter.set_custom_rules(self._highlight_rules)


        self.log_text_edit.verticalScrollBar().valueChanged.connect(self._on_scroll_bar_moved)
        self.log_text_edit.verticalScrollBar().rangeChanged.connect(self._on_scroll_bar_range_changed)

        button_layout = QtWidgets.QHBoxLayout()

        self.zoom_in_button = QtWidgets.QPushButton("Zoom In (+)")
        self.zoom_in_button.clicked.connect(self._zoom_in)
        button_layout.addWidget(self.zoom_in_button)

        self.zoom_out_button = QtWidgets.QPushButton("Zoom Out (-)")
        self.zoom_out_button.clicked.connect(self._zoom_out)
        button_layout.addWidget(self.zoom_out_button)

        # REMOVIDO: Botão "Copiar Seleção"
        # self.copy_selection_button = QtWidgets.QPushButton("📋 Copiar Seleção")
        # self.copy_selection_button.clicked.connect(self._copy_selected_text)
        # button_layout.addWidget(self.copy_selection_button)

        # Botão "Acompanhar em Tempo Real" mudado para "Seguir"
        self.auto_scroll_button = QtWidgets.QPushButton("Seguir")
        self.auto_scroll_button.setCheckable(True)
        self.auto_scroll_button.setChecked(True)
        self.auto_scroll_button.clicked.connect(self._toggle_auto_scroll)
        button_layout.addWidget(self.auto_scroll_button)

        # REMOVIDO: Checkbox "Manter no Topo"
        # self.always_on_top_checkbox = QtWidgets.QCheckBox("Manter no Topo")
        # self.always_on_top_checkbox.stateChanged.connect(self._toggle_always_on_top)
        # button_layout.addWidget(self.always_on_top_checkbox)

        self.highlight_settings_button = QtWidgets.QPushButton("🎨 Realce") # Texto alterado
        self.highlight_settings_button.clicked.connect(self._open_highlight_settings)
        button_layout.addWidget(self.highlight_settings_button)

        self.choose_file_button = QtWidgets.QPushButton("📁 Abrir Outro Log")
        self.choose_file_button.clicked.connect(self._choose_new_log_file)
        button_layout.addWidget(self.choose_file_button)

        right_layout.addLayout(button_layout)
        splitter.addWidget(right_panel_widget)

        # Definir tamanhos iniciais para os painéis (25% para a lista, 75% para o log)
        splitter.setSizes([300, 900])

        self.thread = None
        self.log_reader = None
        self._init_log_reader_worker()
        self._load_log_files_from_directory()

        app_logger.info(f"LogViewerDialog inicializado para diretório: {self.initial_log_directory}")


    def _init_log_reader_worker(self):
        """Inicializa o worker LogFileReader em uma nova thread."""
        if self.log_reader:
            self.log_reader.stop_monitoring()
            self.log_reader.deleteLater()
        if self.thread and self.thread.isRunning():
            self.thread.quit()
            self.thread.wait(2000)
            if self.thread.isRunning():
                self.thread.terminate()
            self.thread.deleteLater()

        self.thread = QtCore.QThread()
        self.log_reader = LogFileReader()
        self.log_reader.moveToThread(self.thread)

        self.thread.started.connect(self.log_reader.start_monitoring)
        self.log_reader.filtered_full_log.connect(self._set_current_log_content)
        self.log_reader.new_log_lines.connect(self.append_log_lines)
        self.log_reader.error_occurred.connect(self.handle_reader_error)
        self.log_reader.finished.connect(self.thread.quit)
        self.log_reader.file_loaded.connect(self._reset_viewer_for_new_file)

        self.thread.start()


    def _load_log_files_from_directory(self):
        """Carrega a lista de arquivos de log do diretório inicial ou de um diretório escolhido."""
        self.file_list_widget.clear()
        self.current_log_file_path = None
        self.log_text_edit.clear()
        self.setWindowTitle(f"WebBatman - Visualizador de Log")

        directory_to_scan = self.initial_log_directory

        if not os.path.isdir(directory_to_scan):
            self.handle_reader_error(f"Erro: O caminho de logs '{directory_to_scan}' não é um diretório válido.")
            app_logger.error(f"Caminho de log inválido: {directory_to_scan}")
            return

        log_files_info = []
        try:
            for filename in os.listdir(directory_to_scan):
                full_path = os.path.join(directory_to_scan, filename)
                if os.path.isfile(full_path) and os.access(full_path, os.R_OK):
                    if filename.lower().endswith(('.log', '.txt', '.out', '.err', '.trace')) or "." not in filename:
                        try:
                            mod_time = os.path.getmtime(full_path)
                            log_files_info.append((filename, full_path, mod_time))
                        except Exception as e:
                            app_logger.warning(f"Não foi possível obter data de modificação para '{filename}': {e}")
                            log_files_info.append((filename, full_path, 0))

            log_files_info.sort(key=lambda x: x[2], reverse=True)

            for filename, full_path, mod_time in log_files_info:
                item = QtWidgets.QListWidgetItem(filename)
                item.setData(QtCore.Qt.UserRole, full_path)
                self.file_list_widget.addItem(item)

            app_logger.info(f"Carregados {len(log_files_info)} arquivos de log do diretório: {directory_to_scan}")

            if log_files_info:
                self.file_list_widget.setCurrentRow(0)
                self._on_log_file_selected(self.file_list_widget.item(0))

        except Exception as e:
            self.handle_reader_error(f"Erro ao listar arquivos de log em '{directory_to_scan}': {e}")
            app_logger.critical(f"Erro ao listar arquivos: {e}", exc_info=True)


    def _filter_log_files(self, text):
        search_text = text.lower()
        for i in range(self.file_list_widget.count()):
            item = self.file_list_widget.item(i)
            item_text = item.text().lower()
            item.setHidden(search_text not in item_text)


    def _on_log_file_selected(self, item):
        if item:
            selected_file_name = item.text()
            full_file_path = item.data(QtCore.Qt.UserRole)
            app_logger.info(f"Arquivo selecionado: {full_file_path}")

            if full_file_path == self.current_log_file_path:
                app_logger.info(f"Arquivo '{selected_file_name}' já está sendo monitorado. Nenhuma ação necessária.")
                return

            self.current_log_file_path = full_file_path
            self.log_reader.set_log_file(full_file_path)
            self.setWindowTitle(f"WebBatman - Visualizador de Log: {selected_file_name}")


    # --- Métodos de Busca ---
    def _reset_search(self):
        self._last_found_cursor = None
        self._search_term = self.search_input.text()
        self._search_case_sensitive = self.search_case_sensitive_checkbox.isChecked()
        self.highlighter.set_search_pattern(self._search_term, self._search_case_sensitive)
        self.log_text_edit.setTextCursor(QtGui.QTextCursor(self.log_text_edit.document()))


    def _find_text(self, options):
        search_text = self.search_input.text()
        if not search_text:
            self.highlighter.set_search_pattern("")
            return

        flags = options
        if self.search_case_sensitive_checkbox.isChecked():
            flags |= QtGui.QTextDocument.FindCaseSensitively
        else:
            flags &= ~QtGui.QTextDocument.FindCaseSensitively

        self.highlighter.set_search_pattern(search_text, self.search_case_sensitive_checkbox.isChecked())

        cursor = self.log_text_edit.textCursor()

        start_position = cursor.position()
        if options == QtGui.QTextDocument.FindBackward:
            if cursor.hasSelection():
                start_position = cursor.selectionStart()
            if start_position == 0 and not cursor.hasSelection():
                start_position = self.log_text_edit.document().characterCount()
        else:
            if cursor.hasSelection():
                start_position = cursor.selectionEnd()
            if start_position >= self.log_text_edit.document().characterCount():
                start_position = 0


        found_cursor = self.log_text_edit.document().find(search_text, start_position, flags)

        if found_cursor.isNull():
            if options == QtGui.QTextDocument.FindNext:
                found_cursor = self.log_text_edit.document().find(search_text, 0, flags)
            else:
                found_cursor = self.log_text_edit.document().find(search_text, self.log_text_edit.document().characterCount(), flags | QtGui.QTextDocument.FindBackward)

            if found_cursor.isNull() or (found_cursor.position() == cursor.position() and not cursor.hasSelection()):
                QtWidgets.QMessageBox.information(self, "Busca", f"O termo '{search_text}' não foi encontrado.")
                self.highlighter.set_search_pattern("")
                return
            else:
                QtWidgets.QMessageBox.information(self, "Busca", "Fim do documento. Reiniciando a busca do início.")


        self.log_text_edit.setTextCursor(found_cursor)
        self._last_found_cursor = found_cursor

    # --- Métodos de Filtro ---
    def _apply_filter(self):
        self._filter_term = self.filter_input.text()
        self._filter_mode = self.filter_mode_combo.currentData()

        if self.log_reader:
            self.log_reader.set_filter(self._filter_term, self._filter_mode)

    # --- Métodos de Zoom ---
    def _zoom_in(self):
        if self._current_font_size < 20:
            self._current_font_size += 1
            self.log_text_edit.setFontPointSize(self._current_font_size)

    def _zoom_out(self):
        if self._current_font_size > 8:
            self._current_font_size -= 1
            self.log_text_edit.setFontPointSize(self._current_font_size)

    # --- REMOVIDO: Métodos de "Always on Top" ---
    # def _toggle_always_on_top(self, state):
    #     if state == QtCore.Qt.Checked:
    #         self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
    #     else:
    #         self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowStaysOnTopHint)
    #     self.show()

    # --- Métodos de Realce Personalizado ---
    def _load_custom_highlight_rules(self):
        rules_file = "highlight_rules.json"
        if os.path.exists(rules_file):
            try:
                with open(rules_file, 'r', encoding='utf-8') as f:
                    self._highlight_rules = json.load(f)
            except Exception as e:
                app_logger.error(f"Erro ao carregar regras de realce de '{rules_file}': {e}")
                self._highlight_rules = []
        else:
            self._highlight_rules = []

    def _open_highlight_settings(self):
        dialog = HighlightSettingsDialog(list(self._highlight_rules), self)
        dialog.settings_changed.connect(self._update_highlight_rules)
        dialog.exec_()

    def _update_highlight_rules(self, new_rules):
        self._highlight_rules = new_rules
        self.highlighter.set_custom_rules(self._highlight_rules)
        self.highlighter.rehighlight()

    def _reset_viewer_for_new_file(self):
        """Reinicia o visualizador quando um novo arquivo é carregado pelo LogFileReader."""
        self.log_text_edit.clear()
        self.auto_scroll_enabled = True
        self.auto_scroll_button.setChecked(True)
        self.filter_input.clear()
        self._filter_term = ""
        self.filter_mode_combo.setCurrentIndex(0)
        self._filter_mode = "include"
        self.search_input.clear()
        self._search_term = ""
        self._last_found_cursor = None
        self.highlighter.set_search_pattern("")
        self.highlighter.rehighlight()


    def _set_current_log_content(self, lines):
        """Define o conteúdo TOTAL do QTextEdit (usado para carga inicial e mudanças de filtro)."""
        self.log_text_edit.verticalScrollBar().valueChanged.disconnect(self._on_scroll_bar_moved)
        self.log_text_edit.verticalScrollBar().rangeChanged.disconnect(self._on_scroll_bar_range_changed)

        self.log_text_edit.clear()
        self.log_text_edit.setText("\n".join(lines))

        self.log_text_edit.verticalScrollBar().valueChanged.connect(self._on_scroll_bar_moved)
        self.log_text_edit.verticalScrollBar().rangeChanged.connect(self._on_scroll_bar_range_changed)

        self.highlighter.set_search_pattern(self._search_term, self._search_case_sensitive)
        self.highlighter.rehighlight()

        if self.auto_scroll_enabled:
            self._force_scroll_to_bottom()


    def _on_scroll_bar_moved(self, value):
        scrollbar = self.log_text_edit.verticalScrollBar()
        if scrollbar.maximum() - value > 20:
            if self.auto_scroll_enabled:
                self.auto_scroll_enabled = False
                self.auto_scroll_button.setChecked(False)
        else:
            if not self.auto_scroll_enabled and scrollbar.value() == scrollbar.maximum():
                self.auto_scroll_enabled = True
                self.auto_scroll_button.setChecked(True)

    def _on_scroll_bar_range_changed(self, min_val, max_val):
        if self.auto_scroll_enabled:
            self._force_scroll_to_bottom()

    def _force_scroll_to_bottom(self):
        self.log_text_edit.verticalScrollBar().setValue(self.log_text_edit.verticalScrollBar().maximum())
        self.auto_scroll_enabled = True
        self.auto_scroll_button.setChecked(True)

    def _toggle_auto_scroll(self):
        self.auto_scroll_enabled = self.auto_scroll_button.isChecked()
        if self.auto_scroll_enabled:
            self._force_scroll_to_bottom()

    def append_log_lines(self, lines):
        """Adiciona múltiplas novas linhas ao QTextEdit como texto puro."""
        if not lines:
            return

        cursor = self.log_text_edit.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        cursor.insertText("\n" + "\n".join(lines))

        if self.auto_scroll_enabled:
            self._force_scroll_to_bottom()

        for line_number in range(cursor.blockNumber(), cursor.blockNumber() - len(lines) -1, -1):
            block = self.log_text_edit.document().findBlockByNumber(line_number)
            if block.isValid():
                self.highlighter.rehighlightBlock(block)


    # REMOVIDO: _copy_selected_text
    # def _copy_selected_text(self):
    #     selected_text = self.log_text_edit.textCursor().selectedText()
    #     if selected_text:
    #         QtWidgets.QApplication.clipboard().setText(selected_text)
    #         QtWidgets.QMessageBox.information(self, "Copiar", "Texto selecionado copiado para a área de transferência.")
    #     else:
    #         QtWidgets.QMessageBox.warning(self, "Copiar", "Nenhum texto selecionado para copiar.")

    def _choose_new_log_file(self):
        """Permite ao usuário selecionar um NOVO arquivo de log para visualizar."""
        initial_dir = self.current_log_file_path if self.current_log_file_path and os.path.exists(self.current_log_file_path) else \
                      self.initial_log_directory if os.path.exists(self.initial_log_directory) else os.getcwd()

        file_dialog = QtWidgets.QFileDialog(self)
        file_dialog.setWindowTitle("Selecionar Novo Arquivo de Log")
        file_dialog.setDirectory(initial_dir)
        file_dialog.setNameFilter("Arquivos de Log (*.log *.txt *.out *.err *.trace);;Todos os Arquivos (*.*)")
        file_dialog.setFileMode(QtWidgets.QFileDialog.ExistingFile)

        if file_dialog.exec_():
            selected_files = file_dialog.selectedFiles()
            if selected_files:
                new_log_path = selected_files[0]
                if self.log_reader:
                    self.log_reader.set_log_file(new_log_path)
                    new_dir = os.path.dirname(new_log_path)
                    if new_dir != self.initial_log_directory:
                        self.initial_log_directory = new_dir
                        self._load_log_files_from_directory()
                        for i in range(self.file_list_widget.count()):
                            item = self.file_list_widget.item(i)
                            if item.data(QtCore.Qt.UserRole) == new_log_path:
                                self.file_list_widget.setCurrentItem(item)
                                break
                else:
                    self._init_log_reader_worker()
                    self.log_reader.set_log_file(new_log_path)
                app_logger.info(f"Usuário escolheu um novo log: {new_log_path}") # Mover esta linha para cá


    def handle_reader_error(self, message):
        QtWidgets.QMessageBox.critical(self, "Erro no Leitor de Log", message)
        app_logger.error(f"Erro do LogFileReader: {message}")

    def closeEvent(self, event):
        if self.log_reader:
            self.log_reader.stop_monitoring()
        if self.thread and self.thread.isRunning():
            self.thread.quit()
            self.thread.wait(2000)
            if self.thread.isRunning():
                self.thread.terminate()
        super().closeEvent(event)