#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: Shared Baseband One Channel
# Author: KJ6DZB
# Description: RTL-SDR shared baseband with one WBFM demod branch
# GNU Radio version: 3.8.1.0

from distutils.version import StrictVersion

if __name__ == '__main__':
    import ctypes
    import sys
    if sys.platform.startswith('linux'):
        try:
            x11 = ctypes.cdll.LoadLibrary('libX11.so')
            x11.XInitThreads()
        except:
            print("Warning: failed to XInitThreads()")

from PyQt5 import Qt
from PyQt5.QtCore import QObject, pyqtSlot
from gnuradio import eng_notation
from gnuradio import qtgui
from gnuradio.filter import firdes
import sip
from gnuradio import analog
from gnuradio import audio
from gnuradio import filter
from gnuradio import gr
import sys
import signal
from argparse import ArgumentParser
from gnuradio.eng_arg import eng_float, intx
from gnuradio.qtgui import Range, RangeWidget
import osmosdr
import time
from gnuradio import qtgui

class shared_baseband_one_channel(gr.top_block, Qt.QWidget):

    def __init__(self):
        gr.top_block.__init__(self, "Shared Baseband One Channel")
        Qt.QWidget.__init__(self)
        self.setWindowTitle("Shared Baseband One Channel")
        qtgui.util.check_set_qss()
        try:
            self.setWindowIcon(Qt.QIcon.fromTheme('gnuradio-grc'))
        except:
            pass
        self.top_scroll_layout = Qt.QVBoxLayout()
        self.setLayout(self.top_scroll_layout)
        self.top_scroll = Qt.QScrollArea()
        self.top_scroll.setFrameStyle(Qt.QFrame.NoFrame)
        self.top_scroll_layout.addWidget(self.top_scroll)
        self.top_scroll.setWidgetResizable(True)
        self.top_widget = Qt.QWidget()
        self.top_scroll.setWidget(self.top_widget)
        self.top_layout = Qt.QVBoxLayout(self.top_widget)
        self.top_grid_layout = Qt.QGridLayout()
        self.top_layout.addLayout(self.top_grid_layout)

        self.settings = Qt.QSettings("GNU Radio", "shared_baseband_one_channel")

        try:
            if StrictVersion(Qt.qVersion()) < StrictVersion("5.0.0"):
                self.restoreGeometry(self.settings.value("geometry").toByteArray())
            else:
                self.restoreGeometry(self.settings.value("geometry"))
        except:
            pass

        ##################################################
        # Variables
        ##################################################
        self.gain_mode0 = gain_mode0 = False
        self.samp_rate = samp_rate = 240000
        self.rf_gain = rf_gain = 25
        self.rf_center_freq = rf_center_freq = 90700000
        self.ppm = ppm = 135
        self.gain_mode0_control = gain_mode0_control = gain_mode0
        self.chan1_transition = chan1_transition = 10000
        self.chan1_offset = chan1_offset = 0
        self.chan1_decim = chan1_decim = 5
        self.chan1_cutoff = chan1_cutoff = 75000
        self.audio_rate = audio_rate = 48000

        ##################################################
        # Blocks
        ##################################################
        self._rf_gain_range = Range(0, 50, 1, 25, 200)
        self._rf_gain_win = RangeWidget(self._rf_gain_range, self.set_rf_gain, 'RF Gain (dB)', "counter_slider", float)
        self.top_grid_layout.addWidget(self._rf_gain_win, 1, 4, 1, 2)
        for r in range(1, 2):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(4, 6):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._rf_center_freq_tool_bar = Qt.QToolBar(self)
        self._rf_center_freq_tool_bar.addWidget(Qt.QLabel('RF Center Frequency (Hz)' + ": "))
        self._rf_center_freq_line_edit = Qt.QLineEdit(str(self.rf_center_freq))
        self._rf_center_freq_tool_bar.addWidget(self._rf_center_freq_line_edit)
        self._rf_center_freq_line_edit.returnPressed.connect(
            lambda: self.set_rf_center_freq(eng_notation.str_to_num(str(self._rf_center_freq_line_edit.text()))))
        self.top_grid_layout.addWidget(self._rf_center_freq_tool_bar, 0, 2, 1, 2)
        for r in range(0, 1):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(2, 4):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._ppm_range = Range(100, 150, 1, 135, 200)
        self._ppm_win = RangeWidget(self._ppm_range, self.set_ppm, 'PPM Correction', "counter_slider", int)
        self.top_grid_layout.addWidget(self._ppm_win, 0, 4, 1, 2)
        for r in range(0, 1):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(4, 6):
            self.top_grid_layout.setColumnStretch(c, 1)
        self._chan1_offset_range = Range(-50000, 50000, 1000, 0, 200)
        self._chan1_offset_win = RangeWidget(self._chan1_offset_range, self.set_chan1_offset, 'Receiver Offset (+/-50 kHz)', "counter_slider", int)
        self.top_grid_layout.addWidget(self._chan1_offset_win, 2, 4, 1, 2)
        for r in range(2, 3):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(4, 6):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.qtgui_waterfall_sink_x_0 = qtgui.waterfall_sink_c(
            1024, #size
            firdes.WIN_BLACKMAN_hARRIS, #wintype
            0, #fc
            samp_rate, #bw
            'Shared Baseband Waterfall (relative)', #name
            1 #number of inputs
        )
        self.qtgui_waterfall_sink_x_0.set_update_time(0.10)
        self.qtgui_waterfall_sink_x_0.enable_grid(False)
        self.qtgui_waterfall_sink_x_0.enable_axis_labels(True)



        labels = ['', '', '', '', '',
                  '', '', '', '', '']
        colors = [0, 0, 0, 0, 0,
                  0, 0, 0, 0, 0]
        alphas = [1.0, 1.0, 1.0, 1.0, 1.0,
                  1.0, 1.0, 1.0, 1.0, 1.0]

        for i in range(1):
            if len(labels[i]) == 0:
                self.qtgui_waterfall_sink_x_0.set_line_label(i, "Data {0}".format(i))
            else:
                self.qtgui_waterfall_sink_x_0.set_line_label(i, labels[i])
            self.qtgui_waterfall_sink_x_0.set_color_map(i, colors[i])
            self.qtgui_waterfall_sink_x_0.set_line_alpha(i, alphas[i])

        self.qtgui_waterfall_sink_x_0.set_intensity_range(-140, 10)

        self._qtgui_waterfall_sink_x_0_win = sip.wrapinstance(self.qtgui_waterfall_sink_x_0.pyqwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(self._qtgui_waterfall_sink_x_0_win, 1, 0, 1, 2)
        for r in range(1, 2):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 2):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.qtgui_freq_sink_x_1 = qtgui.freq_sink_c(
            1024, #size
            firdes.WIN_BLACKMAN_hARRIS, #wintype
            0, #fc
            samp_rate/chan1_decim, #bw
            "", #name
            1
        )
        self.qtgui_freq_sink_x_1.set_update_time(0.10)
        self.qtgui_freq_sink_x_1.set_y_axis(-140, 10)
        self.qtgui_freq_sink_x_1.set_y_label('Receiver 1 Branch FFT', 'dB')
        self.qtgui_freq_sink_x_1.set_trigger_mode(qtgui.TRIG_MODE_FREE, 0.0, 0, "")
        self.qtgui_freq_sink_x_1.enable_autoscale(False)
        self.qtgui_freq_sink_x_1.enable_grid(False)
        self.qtgui_freq_sink_x_1.set_fft_average(1.0)
        self.qtgui_freq_sink_x_1.enable_axis_labels(True)
        self.qtgui_freq_sink_x_1.enable_control_panel(False)

        self.qtgui_freq_sink_x_1.disable_legend()


        labels = ['', '', '', '', '',
            '', '', '', '', '']
        widths = [1, 1, 1, 1, 1,
            1, 1, 1, 1, 1]
        colors = ["blue", "red", "green", "black", "cyan",
            "magenta", "yellow", "dark red", "dark green", "dark blue"]
        alphas = [1.0, 1.0, 1.0, 1.0, 1.0,
            1.0, 1.0, 1.0, 1.0, 1.0]

        for i in range(1):
            if len(labels[i]) == 0:
                self.qtgui_freq_sink_x_1.set_line_label(i, "Data {0}".format(i))
            else:
                self.qtgui_freq_sink_x_1.set_line_label(i, labels[i])
            self.qtgui_freq_sink_x_1.set_line_width(i, widths[i])
            self.qtgui_freq_sink_x_1.set_line_color(i, colors[i])
            self.qtgui_freq_sink_x_1.set_line_alpha(i, alphas[i])

        self._qtgui_freq_sink_x_1_win = sip.wrapinstance(self.qtgui_freq_sink_x_1.pyqwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(self._qtgui_freq_sink_x_1_win, 2, 0, 1, 2)
        for r in range(2, 3):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 2):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.qtgui_freq_sink_x_0 = qtgui.freq_sink_c(
            1024, #size
            firdes.WIN_BLACKMAN_hARRIS, #wintype
            0, #fc
            samp_rate, #bw
            "", #name
            1
        )
        self.qtgui_freq_sink_x_0.set_update_time(0.10)
        self.qtgui_freq_sink_x_0.set_y_axis(-140, 10)
        self.qtgui_freq_sink_x_0.set_y_label('Shared Baseband FFT (relative)', 'dB')
        self.qtgui_freq_sink_x_0.set_trigger_mode(qtgui.TRIG_MODE_FREE, 0.0, 0, "")
        self.qtgui_freq_sink_x_0.enable_autoscale(False)
        self.qtgui_freq_sink_x_0.enable_grid(False)
        self.qtgui_freq_sink_x_0.set_fft_average(1.0)
        self.qtgui_freq_sink_x_0.enable_axis_labels(True)
        self.qtgui_freq_sink_x_0.enable_control_panel(False)

        self.qtgui_freq_sink_x_0.disable_legend()


        labels = ['', '', '', '', '',
            '', '', '', '', '']
        widths = [1, 1, 1, 1, 1,
            1, 1, 1, 1, 1]
        colors = ["blue", "red", "green", "black", "cyan",
            "magenta", "yellow", "dark red", "dark green", "dark blue"]
        alphas = [1.0, 1.0, 1.0, 1.0, 1.0,
            1.0, 1.0, 1.0, 1.0, 1.0]

        for i in range(1):
            if len(labels[i]) == 0:
                self.qtgui_freq_sink_x_0.set_line_label(i, "Data {0}".format(i))
            else:
                self.qtgui_freq_sink_x_0.set_line_label(i, labels[i])
            self.qtgui_freq_sink_x_0.set_line_width(i, widths[i])
            self.qtgui_freq_sink_x_0.set_line_color(i, colors[i])
            self.qtgui_freq_sink_x_0.set_line_alpha(i, alphas[i])

        self._qtgui_freq_sink_x_0_win = sip.wrapinstance(self.qtgui_freq_sink_x_0.pyqwidget(), Qt.QWidget)
        self.top_grid_layout.addWidget(self._qtgui_freq_sink_x_0_win, 0, 0, 1, 2)
        for r in range(0, 1):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(0, 2):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.osmosdr_source_0 = osmosdr.source(
            args="numchan=" + str(1) + " " + "numchan=1 rtl=0"
        )
        self.osmosdr_source_0.set_sample_rate(samp_rate)
        self.osmosdr_source_0.set_center_freq(rf_center_freq, 0)
        self.osmosdr_source_0.set_freq_corr(ppm, 0)
        self.osmosdr_source_0.set_gain(rf_gain, 0)
        self.osmosdr_source_0.set_if_gain(20, 0)
        self.osmosdr_source_0.set_bb_gain(20, 0)
        self.osmosdr_source_0.set_antenna('', 0)
        self.osmosdr_source_0.set_bandwidth(0, 0)
        # Create the options list
        self._gain_mode0_control_options = (gain_mode0, True, )
        # Create the labels list
        self._gain_mode0_control_labels = ('Manual', 'Automatic', )
        # Create the combo box
        self._gain_mode0_control_tool_bar = Qt.QToolBar(self)
        self._gain_mode0_control_tool_bar.addWidget(Qt.QLabel('RF Gain Mode' + ": "))
        self._gain_mode0_control_combo_box = Qt.QComboBox()
        self._gain_mode0_control_tool_bar.addWidget(self._gain_mode0_control_combo_box)
        for _label in self._gain_mode0_control_labels: self._gain_mode0_control_combo_box.addItem(_label)
        self._gain_mode0_control_callback = lambda i: Qt.QMetaObject.invokeMethod(self._gain_mode0_control_combo_box, "setCurrentIndex", Qt.Q_ARG("int", self._gain_mode0_control_options.index(i)))
        self._gain_mode0_control_callback(self.gain_mode0_control)
        self._gain_mode0_control_combo_box.currentIndexChanged.connect(
            lambda i: self.set_gain_mode0_control(self._gain_mode0_control_options[i]))
        # Create the radio buttons
        self.top_grid_layout.addWidget(self._gain_mode0_control_tool_bar, 1, 2, 1, 2)
        for r in range(1, 2):
            self.top_grid_layout.setRowStretch(r, 1)
        for c in range(2, 4):
            self.top_grid_layout.setColumnStretch(c, 1)
        self.freq_xlating_fir_filter_xxx_0 = filter.freq_xlating_fir_filter_ccf(chan1_decim, firdes.low_pass(1.0, samp_rate, chan1_cutoff, chan1_transition), chan1_offset, samp_rate)
        self.audio_sink_0 = audio.sink(audio_rate, "", True)
        self.analog_wfm_rcv_0 = analog.wfm_rcv(
        	quad_rate=samp_rate/chan1_decim,
        	audio_decimation=1,
        )



        ##################################################
        # Connections
        ##################################################
        self.connect((self.analog_wfm_rcv_0, 0), (self.audio_sink_0, 0))
        self.connect((self.freq_xlating_fir_filter_xxx_0, 0), (self.analog_wfm_rcv_0, 0))
        self.connect((self.freq_xlating_fir_filter_xxx_0, 0), (self.qtgui_freq_sink_x_1, 0))
        self.connect((self.osmosdr_source_0, 0), (self.freq_xlating_fir_filter_xxx_0, 0))
        self.connect((self.osmosdr_source_0, 0), (self.qtgui_freq_sink_x_0, 0))
        self.connect((self.osmosdr_source_0, 0), (self.qtgui_waterfall_sink_x_0, 0))

    def closeEvent(self, event):
        self.settings = Qt.QSettings("GNU Radio", "shared_baseband_one_channel")
        self.settings.setValue("geometry", self.saveGeometry())
        event.accept()

    def get_gain_mode0(self):
        return self.gain_mode0

    def set_gain_mode0(self, gain_mode0):
        self.gain_mode0 = gain_mode0
        self.set_gain_mode0_control(self.gain_mode0)
        self.osmosdr_source_0.set_gain_mode(self.gain_mode0, 0)

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.osmosdr_source_0.set_sample_rate(self.samp_rate)
        self.qtgui_freq_sink_x_0.set_frequency_range(0, self.samp_rate)
        self.qtgui_waterfall_sink_x_0.set_frequency_range(0, self.samp_rate)
        self.freq_xlating_fir_filter_xxx_0.set_taps(firdes.low_pass(1.0, self.samp_rate, self.chan1_cutoff, self.chan1_transition))
        self.qtgui_freq_sink_x_1.set_frequency_range(0, self.samp_rate/self.chan1_decim)

    def get_rf_gain(self):
        return self.rf_gain

    def set_rf_gain(self, rf_gain):
        self.rf_gain = rf_gain
        self.osmosdr_source_0.set_gain(self.rf_gain, 0)

    def get_rf_center_freq(self):
        return self.rf_center_freq

    def set_rf_center_freq(self, rf_center_freq):
        self.rf_center_freq = rf_center_freq
        Qt.QMetaObject.invokeMethod(self._rf_center_freq_line_edit, "setText", Qt.Q_ARG("QString", eng_notation.num_to_str(self.rf_center_freq)))
        self.osmosdr_source_0.set_center_freq(self.rf_center_freq, 0)

    def get_ppm(self):
        return self.ppm

    def set_ppm(self, ppm):
        self.ppm = ppm
        self.osmosdr_source_0.set_freq_corr(self.ppm, 0)

    def get_gain_mode0_control(self):
        return self.gain_mode0_control

    def set_gain_mode0_control(self, gain_mode0_control):
        self.gain_mode0_control = gain_mode0_control
        self._gain_mode0_control_callback(self.gain_mode0_control)

    def get_chan1_transition(self):
        return self.chan1_transition

    def set_chan1_transition(self, chan1_transition):
        self.chan1_transition = chan1_transition
        self.freq_xlating_fir_filter_xxx_0.set_taps(firdes.low_pass(1.0, self.samp_rate, self.chan1_cutoff, self.chan1_transition))

    def get_chan1_offset(self):
        return self.chan1_offset

    def set_chan1_offset(self, chan1_offset):
        self.chan1_offset = chan1_offset
        self.freq_xlating_fir_filter_xxx_0.set_center_freq(self.chan1_offset)

    def get_chan1_decim(self):
        return self.chan1_decim

    def set_chan1_decim(self, chan1_decim):
        self.chan1_decim = chan1_decim
        self.qtgui_freq_sink_x_1.set_frequency_range(0, self.samp_rate/self.chan1_decim)

    def get_chan1_cutoff(self):
        return self.chan1_cutoff

    def set_chan1_cutoff(self, chan1_cutoff):
        self.chan1_cutoff = chan1_cutoff
        self.freq_xlating_fir_filter_xxx_0.set_taps(firdes.low_pass(1.0, self.samp_rate, self.chan1_cutoff, self.chan1_transition))

    def get_audio_rate(self):
        return self.audio_rate

    def set_audio_rate(self, audio_rate):
        self.audio_rate = audio_rate



def main(top_block_cls=shared_baseband_one_channel, options=None):

    if StrictVersion("4.5.0") <= StrictVersion(Qt.qVersion()) < StrictVersion("5.0.0"):
        style = gr.prefs().get_string('qtgui', 'style', 'raster')
        Qt.QApplication.setGraphicsSystem(style)
    qapp = Qt.QApplication(sys.argv)

    tb = top_block_cls()
    tb.start()
    tb.show()

    def sig_handler(sig=None, frame=None):
        Qt.QApplication.quit()

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    timer = Qt.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    def quitting():
        tb.stop()
        tb.wait()
    qapp.aboutToQuit.connect(quitting)
    qapp.exec_()


if __name__ == '__main__':
    main()
