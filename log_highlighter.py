# log_highlighter.py
from PyQt5 import QtGui, QtCore

class LogHighlighter(QtGui.QSyntaxHighlighter):
    def __init__(self, parent_document):
        super().__init__(parent_document)
        self._highlighting_rules = []
        self._search_highlight_rules = []

        # Formatos padrão (Nord Theme)
        self._default_error_format = QtGui.QTextCharFormat()
        self._default_error_format.setForeground(QtGui.QColor("#BF616A")) # Nord Red
        self._default_error_format.setFontWeight(QtGui.QFont.Bold)

        self._default_warning_format = QtGui.QTextCharFormat()
        self._default_warning_format.setForeground(QtGui.QColor("#EBCB8B")) # Nord Yellow

        self._default_info_format = QtGui.QTextCharFormat()
        self._default_info_format.setForeground(QtGui.QColor("#8FBCBB")) # Nord Aqua

        self._default_other_format = QtGui.QTextCharFormat()
        self._default_other_format.setForeground(QtGui.QColor("#81A1C1")) # Nord Frost Dark Blue

        self.load_default_rules()

        # Formato para realce de busca
        self._search_format = QtGui.QTextCharFormat()
        self._search_format.setBackground(QtGui.QColor("#A3BE8C")) # Nord Green (Fundo, mais contraste)
        self._search_format.setForeground(QtGui.QColor("#2E3440")) # Nord Darkest (Texto)
        self._search_format.setFontWeight(QtGui.QFont.Bold) # Deixa a busca em negrito

    def load_default_rules(self):
        # Limpa as regras existentes antes de carregar as padrão
        self._highlighting_rules = [
            (QtCore.QRegExp(r"\b(ERROR|ERRO|EXCEPTION|FALHA|CRITICAL)\b", QtCore.Qt.CaseInsensitive), self._default_error_format),
            (QtCore.QRegExp(r"\b(WARNING|WARN|AVISO)\b", QtCore.Qt.CaseInsensitive), self._default_warning_format),
            (QtCore.QRegExp(r"\b(INFO)\b", QtCore.Qt.CaseInsensitive), self._default_info_format),
            (QtCore.QRegExp(r"\b(DEBUG|TRACE)\b", QtCore.Qt.CaseInsensitive), self._default_other_format),
        ]
        self.rehighlight()

    def set_custom_rules(self, rules_data):
        self._highlighting_rules = []
        for rule in rules_data:
            try:
                # Usa QRegExp.ExactMatch para correspondência exata se não for regex,
                # mas normalmente para highlight, regex é mais flexível.
                # Se quiser correspondência de palavra inteira sem regex, precisaria de uma flag extra.
                pattern = QtCore.QRegExp(rule['pattern'], QtCore.Qt.CaseSensitive if rule.get('case_sensitive', False) else QtCore.Qt.CaseInsensitive)
                format = QtGui.QTextCharFormat()
                format.setForeground(QtGui.QColor(rule['color']))
                if rule.get('bold', False):
                    format.setFontWeight(QtGui.QFont.Bold)
                if rule.get('italic', False):
                    format.setFontItalic(True)
                if rule.get('background', None):
                    format.setBackground(QtGui.QColor(rule['background']))
                self._highlighting_rules.append((pattern, format))
            except Exception as e:
                print(f"Erro ao carregar regra personalizada: {e} - Rule: {rule}")
        self.rehighlight() # Aplica as novas regras imediatamente

    def set_search_pattern(self, pattern, case_sensitive=False):
        self._search_highlight_rules = []
        if pattern:
            # Use QRegExp.FixedString para uma busca literal que não interpreta metacaracteres de regex
            # Se você quer que o usuário possa digitar regex na busca, remova QtCore.QRegExp.FixedString
            regexp = QtCore.QRegExp(pattern, QtCore.Qt.CaseSensitive if case_sensitive else QtCore.Qt.CaseInsensitive, QtCore.QRegExp.FixedString)
            self._search_highlight_rules.append((regexp, self._search_format))
        self.rehighlight() # Re-aplica todo o realce (busca e regras normais)

    def highlightBlock(self, text):
        """
        Método sobrescrito para aplicar o realce a um bloco de texto (uma linha).
        Prioriza realce de busca sobre regras de log.
        """
        # Aplica as regras de realce de log primeiro
        for pattern, format in self._highlighting_rules:
            expression = QtCore.QRegExp(pattern)
            index = expression.indexIn(text)
            while index >= 0:
                length = expression.matchedLength()
                self.setFormat(index, length, format)
                index = expression.indexIn(text, index + length)

        # Aplica o realce de busca por último (ele deve sobrescrever os formatos de log)
        for pattern, format in self._search_highlight_rules:
            expression = QtCore.QRegExp(pattern)
            index = expression.indexIn(text)
            while index >= 0:
                length = expression.matchedLength()
                self.setFormat(index, length, format)
                index = expression.indexIn(text, index + length)

        self.setCurrentBlockState(0)