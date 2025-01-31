# -*- coding: utf-8 -*-
# Copyright (c) 2020-2022 Salvador E. Tropea
# Copyright (c) 2020-2022 Instituto Nacional de Tecnología Industrial
# License: GPL-3.0
# Project: KiBot (formerly KiPlot)
"""
Dependencies:
  - from: KiAuto
    role: mandatory
    version: 2.0.0
"""
import os
from sys import exit
from .macros import macros, pre_class  # noqa: F401
from .error import KiPlotConfigurationError
from .gs import GS
from .optionable import Optionable
from .kiplot import exec_with_retry, load_board, add_extra_options
from .misc import DRC_ERROR
from .log import get_logger

logger = get_logger(__name__)


@pre_class
class Run_DRC(BasePreFlight):  # noqa: F821
    """ [boolean=false] Runs the DRC (Distance Rules Check). To ensure we have a valid PCB.
        The report file name is controlled by the global output pattern (%i=drc %x=txt).
        Note that the KiCad 6 *Test for parity between PCB and schematic* option is not supported.
        If you need to check the parity use the `update_xml` preflight """
    def __init__(self, name, value):
        super().__init__(name, value)
        if not isinstance(value, bool):
            raise KiPlotConfigurationError('must be boolean')
        self._enabled = value
        self._pcb_related = True
        self._expand_id = 'drc'
        self._expand_ext = 'txt'

    def get_targets(self):
        """ Returns a list of targets generated by this preflight """
        load_board()
        out_pattern = GS.global_output if GS.global_output is not None else GS.def_global_output
        name = Optionable.expand_filename_pcb(self, out_pattern)
        out_dir = self.expand_dirname(GS.out_dir)
        if GS.global_dir and GS.global_use_dir_for_preflights:
            out_dir = os.path.join(out_dir, self.expand_dirname(GS.global_dir))
        return [os.path.abspath(os.path.join(out_dir, name))]

    def run(self):
        command = self.ensure_tool('KiAuto')
        output = self.get_targets()[0]
        os.makedirs(os.path.dirname(output), exist_ok=True)
        logger.debug('DRC report: '+output)
        cmd = [command, 'run_drc', '-o', output]
        if GS.filter_file:
            cmd.extend(['-f', GS.filter_file])
        if GS.global_drc_exclusions_workaround:
            cmd.append('-F')
        if BasePreFlight.get_option('ignore_unconnected'):  # noqa: F821
            cmd.append('-i')
        cmd.extend([GS.pcb_file, self.expand_dirname(GS.out_dir)])
        # If we are in verbose mode enable debug in the child
        cmd, video_remove = add_extra_options(cmd)
        logger.info('- Running the DRC')
        ret = exec_with_retry(cmd)
        if video_remove:
            video_name = os.path.join(self.expand_dirname(GS.out_dir), 'pcbnew_run_drc_screencast.ogv')
            if os.path.isfile(video_name):
                os.remove(video_name)
        if ret:
            if ret > 127:
                ret = -(256-ret)
            if ret < 0:
                logger.error('DRC errors: %d', -ret)
            else:
                logger.error('DRC returned %d', ret)
            exit(DRC_ERROR)
