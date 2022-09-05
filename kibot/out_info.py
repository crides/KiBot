# -*- coding: utf-8 -*-
# Copyright (c) 2022 Salvador E. Tropea
# Copyright (c) 2022 Instituto Nacional de Tecnología Industrial
# License: GPL-3.0
# Project: KiBot (formerly KiPlot)
import os
import sys
from .gs import GS
from .optionable import BaseOptions
from .kiplot import run_command
from .macros import macros, document, output_class  # noqa: F401
from . import log

logger = log.get_logger()


class InfoOptions(BaseOptions):
    def __init__(self):
        with document:
            self.output = GS.def_global_output
            """ *Filename for the output (%i=info, %x=txt) """
        super().__init__()
        self._expand_id = 'info'
        self._expand_ext = 'txt'
        self._none_related = True

    def get_targets(self, out_dir):
        return [self._parent.expand_filename(out_dir, self.output)]

    def run(self, name):
        dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        ret = run_command([os.path.join(dir, 'kibot-check')])
        with open(name, 'wt') as f:
            f.write(ret+'\n')


@output_class
class Info(BaseOutput):  # noqa: F821
    """ Info
        Records information about the current run.
        It can be used to know more about the environment used to generate the files.
        Please don't rely on the way things are reported, its content could change,
        adding or removing information.
        It current shows the `kibot-check` output """
    def __init__(self):
        super().__init__()
        self._category = ['PCB/docs', 'Schematic/docs']
        with document:
            self.options = InfoOptions
            """ *[dict] Options for the `info` output """

    @staticmethod
    def get_conf_examples(name, layers, templates):
        return BaseOutput.simple_conf_examples(name, 'Information about the run', '.')  # noqa: F821
