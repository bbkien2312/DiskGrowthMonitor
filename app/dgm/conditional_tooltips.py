"""Show textual tooltips only when the displayed text is actually clipped."""
from PySide6.QtCore import QObject, QEvent, Qt, QRect
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (QApplication,QLabel,QAbstractButton,QComboBox,QLineEdit,QAbstractItemView,
    QHeaderView,QStyle,QStyleOptionComboBox,QStyleOptionButton,QStyleOptionViewItem,QToolTip,QWidget)


def clipped_text(widget):
    if isinstance(widget,QComboBox):
        option=QStyleOptionComboBox();widget.initStyleOption(option)
        rect=widget.style().subControlRect(QStyle.ComplexControl.CC_ComboBox,option,QStyle.SubControl.SC_ComboBoxEditField,widget)
        return widget.currentText() if widget.fontMetrics().horizontalAdvance(widget.currentText())>rect.width() else ''
    if isinstance(widget,QLineEdit):
        text=widget.displayText() or widget.placeholderText();m=widget.textMargins()
        return text if widget.fontMetrics().horizontalAdvance(text)>widget.contentsRect().width()-m.left()-m.right()-8 else ''
    if isinstance(widget,QAbstractButton):
        text=widget.text()
        if not text:return None  # An icon-only button still needs its action name.
        option=QStyleOptionButton();option.initFrom(widget)
        rect=widget.style().subElementRect(QStyle.SubElement.SE_PushButtonContents,option,widget)
        return text if widget.fontMetrics().horizontalAdvance(text.replace('&',''))>rect.width() else ''
    if isinstance(widget,QLabel):
        text=widget.text()
        if hasattr(widget,'display_lines'):
            shown=' '.join(widget.display_lines());full=' '.join(text.split())
            return text if shown!=full else ''
        rect=widget.contentsRect()
        if widget.wordWrap():
            bounds=widget.fontMetrics().boundingRect(QRect(0,0,rect.width(),100000),Qt.TextFlag.TextWordWrap,text)
            clipped=bounds.height()>rect.height() or bounds.width()>rect.width()
        else:
            displayed=text.replace('\n',' • ') if widget.__class__.__name__=='ElidingLabel' else text
            clipped=max((widget.fontMetrics().horizontalAdvance(line) for line in displayed.splitlines()),default=0)>rect.width()
        return text if clipped else ''
    return ''


class ConditionalTooltips(QObject):
    def __init__(self,app):
        super().__init__(app);app.installEventFilter(self)

    def eventFilter(self,watched,event):
        if event.type()!=QEvent.Type.ToolTip or not isinstance(watched,QWidget):return False
        ancestor=watched
        while ancestor is not None and not ancestor.property('dgmConditionalTooltips'):ancestor=ancestor.parentWidget()
        if ancestor is None:return False
        parent=watched.parentWidget() if hasattr(watched,'parentWidget') else None
        text='';rect=watched.rect() if hasattr(watched,'rect') else QRect()
        if isinstance(parent,QHeaderView):
            index=parent.logicalIndexAt(event.pos())
            if index>=0:
                value=parent.model().headerData(index,parent.orientation(),Qt.ItemDataRole.DisplayRole)
                if parent.fontMetrics().horizontalAdvance(str(value))>parent.sectionSize(index)-24:text=str(value)
        elif isinstance(parent,QAbstractItemView):
            index=parent.indexAt(event.pos())
            if index.isValid():
                option=QStyleOptionViewItem();option.initFrom(parent);option.rect=parent.visualRect(index);option.widget=parent
                delegate=parent.itemDelegateForIndex(index);delegate.initStyleOption(option,index)
                rect=parent.style().subElementRect(QStyle.SubElement.SE_ItemViewItemText,option,parent)
                if QFontMetrics(option.font).horizontalAdvance(option.text)>max(0,rect.width()-6):text=option.text
        else:
            text=clipped_text(watched)
            if text is None:return False
        if text:QToolTip.showText(event.globalPos(),text,watched,rect)
        else:QToolTip.hideText()
        return True


def install_tooltips(window):
    # One application-wide filter, not one retained callback per reopened
    # window. A window tag also covers later dialogs and combobox popups.
    window.setProperty('dgmConditionalTooltips',True)
    app=QApplication.instance()
    if not hasattr(app,'_dgm_tooltip_filter'):app._dgm_tooltip_filter=ConditionalTooltips(app)
    return app._dgm_tooltip_filter
