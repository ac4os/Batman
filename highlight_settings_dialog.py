# highlight_settings_dialog.py
from PyQt5 import QtWidgets, QtGui, QtCore
import json
import os

class HighlightSettingsDialog(QtWidgets.QDialog):
    settings_changed = QtCore.pyqtSignal(list) # Sinal para notificar a janela principal

    def __init__(self, current_rules, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configurações de Realce")
        self.setGeometry(200, 200, 600, 400)
        
        self.current_rules = current_rules # Lista de dicionários para regras
        self.rules_file = "highlight_rules.json"

        # --- NOVO STYLESHEET PARA UM VISUAL MAIS "WINDOWS" ---
        self.setStyleSheet("""
            QDialog {
                background-color: #F0F0F0; /* Fundo cinza claro, típico do Windows */
                color: #333333; /* Texto escuro para contraste no fundo claro */
            }
            QLabel, QCheckBox {
                color: #333333; /* Texto escuro */
            }
            QLineEdit {
                background-color: #FFFFFF; /* Fundo branco para campos de texto */
                color: #333333;
                border: 1px solid #CCCCCC; /* Borda cinza suave */
                border-radius: 3px;
                padding: 3px;
            }
            QPushButton {
                background-color: #E1E1E1; /* Botões cinza claro */
                color: #333333;
                padding: 5px 10px;
                border-radius: 3px;
                border: 1px solid #BFBFBF; /* Borda sutil */
            }
            QPushButton:hover {
                background-color: #D4D4D4; /* Levemente mais escuro no hover */
            }
            QPushButton:pressed {
                background-color: #C8C8C8; /* Mais escuro ao pressionar */
                border: 1px solid #A0A0A0;
            }
            QListWidget {
                background-color: #FFFFFF; /* Fundo branco para a lista */
                color: #333333;
                border: 1px solid #CCCCCC; /* Borda cinza suave */
                border-radius: 5px;
            }
            QListWidget::item {
                padding: 2px; /* Espaçamento interno para itens da lista */
            }
            QListWidget::item:selected {
                background-color: #0078D7; /* Azul padrão de seleção do Windows */
                color: #FFFFFF; /* Texto branco na seleção */
            }
            /* Estilo para QColorDialog (se o tema não for aplicado automaticamente) */
            QColorDialog {
                background-color: #F0F0F0;
                color: #333333;
            }
            QColorDialog QPushButton {
                background-color: #E1E1E1;
                color: #333333;
                border: 1px solid #BFBFBF;
                border-radius: 3px;
            }
            QColorDialog QPushButton:hover {
                background-color: #D4D4D4;
            }
        """)

        main_layout = QtWidgets.QVBoxLayout(self)

        self.rules_list_widget = QtWidgets.QListWidget()
        self.rules_list_widget.itemSelectionChanged.connect(self._update_rule_form)
        main_layout.addWidget(self.rules_list_widget)

        # Formulário para adicionar/editar regras
        form_layout = QtWidgets.QFormLayout()
        self.pattern_input = QtWidgets.QLineEdit()
        self.color_button = QtWidgets.QPushButton("Selecionar Cor")
        self.color_button.clicked.connect(self._choose_color)
        self.selected_color_label = QtWidgets.QLabel("#FFFFFF") # Exibe a cor selecionada
        
        self.background_color_button = QtWidgets.QPushButton("Cor de Fundo (Opcional)")
        self.background_color_button.clicked.connect(self._choose_background_color)
        self.selected_background_color_label = QtWidgets.QLabel("Nenhuma")

        self.bold_checkbox = QtWidgets.QCheckBox("Negrito")
        self.italic_checkbox = QtWidgets.QCheckBox("Itálico")
        self.case_sensitive_checkbox = QtWidgets.QCheckBox("Sensível a Maiúsculas/Minúsculas")

        form_layout.addRow("Padrão (Regex):", self.pattern_input)
        form_layout.addRow("Cor do Texto:", self.color_button)
        form_layout.addRow("Cor Selecionada:", self.selected_color_label)
        form_layout.addRow("Cor de Fundo:", self.background_color_button)
        form_layout.addRow("Cor de Fundo Selecionada:", self.selected_background_color_label)
        form_layout.addRow(self.bold_checkbox)
        form_layout.addRow(self.italic_checkbox)
        form_layout.addRow(self.case_sensitive_checkbox)

        main_layout.addLayout(form_layout)

        # Botões de ação
        button_layout = QtWidgets.QHBoxLayout()
        self.add_button = QtWidgets.QPushButton("Adicionar Regra")
        self.add_button.clicked.connect(self._add_rule)
        self.update_button = QtWidgets.QPushButton("Atualizar Regra Selecionada")
        self.update_button.clicked.connect(self._update_selected_rule)
        self.remove_button = QtWidgets.QPushButton("Remover Regra Selecionada")
        self.remove_button.clicked.connect(self._remove_rule)
        
        button_layout.addWidget(self.add_button)
        button_layout.addWidget(self.update_button)
        button_layout.addWidget(self.remove_button)
        main_layout.addLayout(button_layout)

        self.load_rules_from_file() # Tenta carregar regras salvas
        self._populate_rules_list()
        self._clear_form() # Limpa o formulário no início

    def _populate_rules_list(self):
        self.rules_list_widget.clear()
        for i, rule in enumerate(self.current_rules):
            item_text = f"{i+1}. '{rule['pattern']}' - Cor: {rule['color']}"
            if rule.get('background'):
                item_text += f", Fundo: {rule['background']}"
            if rule.get('bold'):
                item_text += ", Negrito"
            if rule.get('italic'):
                item_text += ", Itálico"
            if rule.get('case_sensitive'):
                item_text += ", Case Sensitive"
            self.rules_list_widget.addItem(item_text)

    def _update_rule_form(self):
        selected_items = self.rules_list_widget.selectedItems()
        if not selected_items:
            self._clear_form()
            return

        index = self.rules_list_widget.row(selected_items[0])
        rule = self.current_rules[index]

        self.pattern_input.setText(rule.get('pattern', ''))
        # Garante que o texto da cor seja visível no novo fundo claro
        self.selected_color_label.setText(rule.get('color', '#000000')) # Default para preto
        self.selected_color_label.setStyleSheet(f"background-color: {rule.get('color', '#FFFFFF')}; color: #000000; border: 1px solid #CCCCCC;")
        
        bg_color = rule.get('background', '')
        self.selected_background_color_label.setText(bg_color if bg_color else "Nenhuma")
        # Garante que o texto da cor de fundo seja visível
        self.selected_background_color_label.setStyleSheet(f"background-color: {bg_color if bg_color else 'transparent'}; color: #333333; border: 1px solid #CCCCCC;")


        self.bold_checkbox.setChecked(rule.get('bold', False))
        self.italic_checkbox.setChecked(rule.get('italic', False))
        self.case_sensitive_checkbox.setChecked(rule.get('case_sensitive', False))

    def _clear_form(self):
        self.pattern_input.clear()
        # Ajusta cores padrão para o novo tema
        self.selected_color_label.setText("#000000") # Padrão preto para texto
        self.selected_color_label.setStyleSheet("background-color: #F0F0F0; color: #000000; border: 1px solid #CCCCCC;") # Fundo claro
        self.selected_background_color_label.setText("Nenhuma")
        self.selected_background_color_label.setStyleSheet("background-color: transparent; color: #333333; border: 1px solid #CCCCCC;")
        self.bold_checkbox.setChecked(False)
        self.italic_checkbox.setChecked(False)
        self.case_sensitive_checkbox.setChecked(False)
        self.rules_list_widget.clearSelection()


    def _add_rule(self):
        pattern = self.pattern_input.text().strip()
        color = self.selected_color_label.text()
        bg_color = self.selected_background_color_label.text()
        bold = self.bold_checkbox.isChecked()
        italic = self.italic_checkbox.isChecked()
        case_sensitive = self.case_sensitive_checkbox.isChecked()

        # Ajuste a validação de cor padrão para #000000 se você a usa como um "não selecionado"
        if not pattern or color == "#000000": # Mudei de #FFFFFF para #000000
            QtWidgets.QMessageBox.warning(self, "Entrada Inválida", "Padrão e Cor são obrigatórios.")
            return
        
        if bg_color == "Nenhuma":
            bg_color = ""

        new_rule = {
            'pattern': pattern,
            'color': color,
            'background': bg_color,
            'bold': bold,
            'italic': italic,
            'case_sensitive': case_sensitive
        }
        self.current_rules.append(new_rule)
        self._populate_rules_list()
        self.settings_changed.emit(self.current_rules)
        self.save_rules_to_file()
        self._clear_form()

    def _update_selected_rule(self):
        selected_items = self.rules_list_widget.selectedItems()
        if not selected_items:
            QtWidgets.QMessageBox.warning(self, "Nenhuma Regra Selecionada", "Selecione uma regra para atualizar.")
            return

        index = self.rules_list_widget.row(selected_items[0])
        
        pattern = self.pattern_input.text().strip()
        color = self.selected_color_label.text()
        bg_color = self.selected_background_color_label.text()
        bold = self.bold_checkbox.isChecked()
        italic = self.italic_checkbox.isChecked()
        case_sensitive = self.case_sensitive_checkbox.isChecked()

        # Ajuste a validação de cor padrão para #000000
        if not pattern or color == "#000000": # Mudei de #FFFFFF para #000000
            QtWidgets.QMessageBox.warning(self, "Entrada Inválida", "Padrão e Cor são obrigatórios.")
            return
        
        if bg_color == "Nenhuma":
            bg_color = ""

        updated_rule = {
            'pattern': pattern,
            'color': color,
            'background': bg_color,
            'bold': bold,
            'italic': italic,
            'case_sensitive': case_sensitive
        }
        self.current_rules[index] = updated_rule
        self._populate_rules_list()
        self.settings_changed.emit(self.current_rules)
        self.save_rules_to_file()
        self._clear_form()


    def _remove_rule(self):
        selected_items = self.rules_list_widget.selectedItems()
        if not selected_items:
            QtWidgets.QMessageBox.warning(self, "Nenhuma Regra Selecionada", "Selecione uma regra para remover.")
            return

        index = self.rules_list_widget.row(selected_items[0])
        del self.current_rules[index]
        self._populate_rules_list()
        self.settings_changed.emit(self.current_rules)
        self.save_rules_to_file()
        self._clear_form()


    def _choose_color(self):
        # Ajusta cor inicial padrão para um tema claro
        initial_color = QtGui.QColor(self.selected_color_label.text()) if self.selected_color_label.text() != "#000000" else QtGui.QColor("#333333")
        color = QtWidgets.QColorDialog.getColor(initial_color, self)
        if color.isValid():
            hex_color = color.name().upper()
            self.selected_color_label.setText(hex_color)
            # Garante texto preto para contraste em cores de fundo claras selecionadas
            self.selected_color_label.setStyleSheet(f"background-color: {hex_color}; color: #000000; border: 1px solid #CCCCCC;")

    def _choose_background_color(self):
        # Ajusta cor inicial padrão para um tema claro
        initial_color = QtGui.QColor(self.selected_background_color_label.text()) if self.selected_background_color_label.text() != "Nenhuma" else QtGui.QColor("#E1E1E1")
        color = QtWidgets.QColorDialog.getColor(initial_color, self)
        if color.isValid():
            hex_color = color.name().upper()
            self.selected_background_color_label.setText(hex_color)
            # Garante texto escuro para contraste em cores de fundo claras selecionadas
            self.selected_background_color_label.setStyleSheet(f"background-color: {hex_color}; color: #333333; border: 1px solid #CCCCCC;")
        else:
            self.selected_background_color_label.setText("Nenhuma")
            self.selected_background_color_label.setStyleSheet("background-color: transparent; color: #333333; border: 1px solid #CCCCCC;")


    def load_rules_from_file(self):
        if os.path.exists(self.rules_file):
            try:
                with open(self.rules_file, 'r', encoding='utf-8') as f:
                    self.current_rules = json.load(f)
                    print(f"Regras de realce carregadas de: {self.rules_file}")
            except Exception as e:
                print(f"Erro ao carregar regras de realce do arquivo {self.rules_file}: {e}")
                self.current_rules = [] # Reset se houver erro
        else:
            print(f"Arquivo de regras '{self.rules_file}' não encontrado. Usando regras padrão.")
            # Não carrega default aqui, será responsabilidade da janela principal
            self.current_rules = []


    def save_rules_to_file(self):
        try:
            with open(self.rules_file, 'w', encoding='utf-8') as f:
                json.dump(self.current_rules, f, indent=4)
            print(f"Regras de realce salvas em: {self.rules_file}")
        except Exception as e:
            print(f"Erro ao salvar regras de realce em {self.rules_file}: {e}")