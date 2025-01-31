# -*- coding: utf-8 -*-
# Copyright (c) 2020-2022 Salvador E. Tropea
# Copyright (c) 2020-2022 Instituto Nacional de Tecnología Industrial
# License: GPL-3.0
# Project: KiBot (formerly KiPlot)
from copy import deepcopy
from glob import glob
import math
import os
import re
from tempfile import NamedTemporaryFile, mkdtemp
from .gs import GS
from .kiplot import load_sch, get_board_comps_data
from .misc import Rect, W_WRONGPASTE, DISABLE_3D_MODEL_TEXT, W_NOCRTYD
if not GS.kicad_version_n:
    # When running the regression tests we need it
    from kibot.__main__ import detect_kicad
    detect_kicad()
if GS.ki6:
    # New name, no alias ...
    from pcbnew import FP_SHAPE, wxPoint, LSET, FP_3DMODEL, ToMM
else:
    from pcbnew import EDGE_MODULE, wxPoint, LSET, MODULE_3D_SETTINGS, ToMM
    FP_3DMODEL = MODULE_3D_SETTINGS
from .registrable import RegOutput
from .optionable import Optionable, BaseOptions
from .fil_base import BaseFilter, apply_fitted_filter, reset_filters, apply_pre_transform
from .kicad.config import KiConf
from .macros import macros, document  # noqa: F401
from .error import KiPlotConfigurationError
from . import log

logger = log.get_logger()
HIGHLIGHT_3D_WRL = """#VRML V2.0 utf8
#KiBot generated highlight
Shape {
  appearance Appearance {
    material DEF RED-01 Material {
      ambientIntensity 0.494
      diffuseColor 1.0 0.0 0.0
      specularColor 0.5 0.0 0.0
      emissiveColor 0.0 0.0 0.0
      transparency 0.5
      shininess 0.25
    }
  }
}
Shape {
  geometry Box { size 1 1 1 }
  appearance Appearance {material USE RED-01 }
}

"""


class BaseOutput(RegOutput):
    def __init__(self):
        super().__init__()
        with document:
            self.name = ''
            """ *Used to identify this particular output definition """
            self.type = ''
            """ *Type of output """
            self.dir = './'
            """ *Output directory for the generated files.
                If it starts with `+` the rest is concatenated to the default dir """
            self.comment = ''
            """ *A comment for documentation purposes """
            self.extends = ''
            """ Copy the `options` section from the indicated output """
            self.run_by_default = True
            """ When enabled this output will be created when no specific outputs are requested """
            self.disable_run_by_default = ''
            """ [string|boolean] Use it to disable the `run_by_default` status of other output.
                Useful when this output extends another and you don't want to generate the original.
                Use the boolean true value to disable the output you are extending """
            self.output_id = ''
            """ Text to use for the %I expansion content. To differentiate variations of this output """
            self.category = Optionable
            """ [string|list(string)=''] The category for this output. If not specified an internally defined category is used.
                Categories looks like file system paths, i.e. PCB/fabrication/gerber """
            self.priority = 50
            """ [0,100] Priority for this output. High priority outputs are created first.
                Internally we use 10 for low priority, 90 for high priority and 50 for most outputs """
        if GS.global_dir:
            self.dir = GS.global_dir
        self._sch_related = False
        self._both_related = False
        self._none_related = False
        self._unkown_is_error = True
        self._done = False
        self._category = None

    @staticmethod
    def attr2longopt(attr):
        return '--'+attr.replace('_', '-')

    def is_sch(self):
        """ True for outputs that works on the schematic """
        return self._sch_related or self._both_related

    def is_pcb(self):
        """ True for outputs that works on the PCB """
        return (not(self._sch_related) and not(self._none_related)) or self._both_related

    def get_targets(self, out_dir):
        """ Returns a list of targets generated by this output """
        if not (hasattr(self, "options") and hasattr(self.options, "get_targets")):
            logger.error("Output {} doesn't implement get_targets(), please report it".format(self))
            return []
        return self.options.get_targets(out_dir)

    def get_dependencies(self):
        """ Returns a list of files needed to create this output """
        if self._sch_related:
            if GS.sch:
                return GS.sch.get_files()
            return [GS.sch_file]
        return [GS.pcb_file]

    def get_extension(self):
        return self.options._expand_ext

    def config(self, parent):
        if self._tree and not self._configured and isinstance(self.extends, str) and self.extends:
            logger.debug("Extending `{}` from `{}`".format(self.name, self.extends))
            # Copy the data from the base output
            out = RegOutput.get_output(self.extends)
            if out is None:
                raise KiPlotConfigurationError('Unknown output `{}` in `extends`'.format(self.extends))
            if out.type != self.type:
                raise KiPlotConfigurationError('Trying to extend `{}` using another type `{}`'.format(out, self))
            if not out._configured:
                # Make sure the extended output is configured, so it can be an extension of another output
                out.config(None)
            if out._tree:
                options = out._tree.get('options', None)
                if options:
                    old_options = self._tree.get('options', {})
                    # logger.error(self.name+" Old options: "+str(old_options))
                    options = deepcopy(options)
                    options.update(old_options)
                    self._tree['options'] = options
                    # logger.error(self.name+" New options: "+str(options))
        super().config(parent)
        to_dis = self.disable_run_by_default
        if isinstance(to_dis, str) and to_dis:  # Skip the boolean case
            out = RegOutput.get_output(to_dis)
            if out is None:
                raise KiPlotConfigurationError('Unknown output `{}` in `disable_run_by_default`'.format(to_dis))
        if self.dir[0] == '+':
            self.dir = (GS.global_dir if GS.global_dir is not None else './') + self.dir[1:]
        if getattr(self, 'options', None) and isinstance(self.options, type):
            # No options, get the defaults
            self.options = self.options()
            # Configure them using an empty tree
            self.options.config(self)
        self.category = self.force_list(self.category)
        if not self.category:
            self.category = self._category

    def expand_dirname(self, out_dir):
        return self.options.expand_filename_both(out_dir, is_sch=self._sch_related)

    def expand_filename(self, out_dir, name):
        name = self.options.expand_filename_both(name, is_sch=self._sch_related)
        return os.path.abspath(os.path.join(out_dir, name))

    @staticmethod
    def get_conf_examples(name, layers, templates):
        return None

    @staticmethod
    def simple_conf_examples(name, comment, dir):
        gb = {}
        outs = [gb]
        gb['name'] = 'basic_'+name
        gb['comment'] = comment
        gb['type'] = name
        gb['dir'] = dir
        return outs

    def fix_priority_help(self):
        self._help_priority = self._help_priority.replace('[number=50]', '[number={}]'.format(self.priority))

    def run(self, output_dir):
        self.output_dir = output_dir
        output = self.options.output if hasattr(self.options, 'output') else ''
        self.options.run(self.expand_filename(output_dir, output))


class BoMRegex(Optionable):
    """ Implements the pair column/regex """
    def __init__(self):
        super().__init__()
        self._unkown_is_error = True
        with document:
            self.column = ''
            """ Name of the column to apply the regular expression """
            self.regex = ''
            """ Regular expression to match """
            self.field = None
            """ {column} """
            self.regexp = None
            """ {regex} """
            self.skip_if_no_field = False
            """ Skip this test if the field doesn't exist """
            self.match_if_field = False
            """ Match if the field exists, no regex applied. Not affected by `invert` """
            self.match_if_no_field = False
            """ Match if the field doesn't exists, no regex applied. Not affected by `invert` """
            self.invert = False
            """ Invert the regex match result """


class VariantOptions(BaseOptions):
    """ BaseOptions plus generic support for variants. """
    def __init__(self):
        with document:
            self.variant = ''
            """ Board variant to apply """
            self.dnf_filter = Optionable
            """ [string|list(string)='_none'] Name of the filter to mark components as not fitted.
                A short-cut to use for simple cases where a variant is an overkill """
            self.pre_transform = Optionable
            """ [string|list(string)='_none'] Name of the filter to transform fields before applying other filters.
                A short-cut to use for simple cases where a variant is an overkill """
        super().__init__()
        self._comps = None
        self.undo_3d_models = {}
        self.undo_3d_models_rep = {}
        self._highlight_3D_file = None
        self._highlighted_3D_components = None

    def config(self, parent):
        super().config(parent)
        self.variant = RegOutput.check_variant(self.variant)
        self.dnf_filter = BaseFilter.solve_filter(self.dnf_filter, 'dnf_filter')
        self.pre_transform = BaseFilter.solve_filter(self.pre_transform, 'pre_transform', is_transform=True)

    def get_refs_hash(self):
        if not self._comps:
            return None
        return {c.ref: c for c in self._comps}

    def get_fitted_refs(self):
        """ List of fitted and included components """
        if not self._comps:
            return []
        return [c.ref for c in self._comps if c.fitted and c.included]

    def get_not_fitted_refs(self):
        """ List of 'not fitted' components, also includes 'not included' """
        if not self._comps:
            return []
        return [c.ref for c in self._comps if not c.fitted or not c.included]

    # Here just to avoid pulling pcbnew for this
    @staticmethod
    def to_mm(val):
        return ToMM(val)

    @staticmethod
    def create_module_element(m):
        if GS.ki6:
            return FP_SHAPE(m)
        return EDGE_MODULE(m)

    @staticmethod
    def cross_module(m, rect, layer):
        """ Draw a cross over a module.
            The rect is a Rect object with the size.
            The layer is which layer id will be used. """
        seg1 = VariantOptions.create_module_element(m)
        seg1.SetWidth(120000)
        seg1.SetStart(wxPoint(rect.x1, rect.y1))
        seg1.SetEnd(wxPoint(rect.x2, rect.y2))
        seg1.SetLayer(layer)
        seg1.SetLocalCoord()  # Update the local coordinates
        m.Add(seg1)
        seg2 = VariantOptions.create_module_element(m)
        seg2.SetWidth(120000)
        seg2.SetStart(wxPoint(rect.x1, rect.y2))
        seg2.SetEnd(wxPoint(rect.x2, rect.y1))
        seg2.SetLayer(layer)
        seg2.SetLocalCoord()  # Update the local coordinates
        m.Add(seg2)
        return [seg1, seg2]

    def cross_modules(self, board, comps_hash):
        """ Draw a cross in all 'not fitted' modules using *.Fab layer """
        if comps_hash is None or not GS.global_cross_footprints_for_dnp:
            return
        # Cross the affected components
        ffab = board.GetLayerID('F.Fab')
        bfab = board.GetLayerID('B.Fab')
        extra_ffab_lines = []
        extra_bfab_lines = []
        for m in GS.get_modules_board(board):
            ref = m.GetReference()
            # Rectangle containing the drawings, no text
            frect = Rect()
            brect = Rect()
            c = comps_hash.get(ref, None)
            if c and c.included and not c.fitted:
                # Meassure the component BBox (only graphics)
                for gi in m.GraphicalItems():
                    if gi.GetClass() == 'MGRAPHIC':
                        l_gi = gi.GetLayer()
                        if l_gi == ffab:
                            frect.Union(gi.GetBoundingBox().getWxRect())
                        if l_gi == bfab:
                            brect.Union(gi.GetBoundingBox().getWxRect())
                # Cross the graphics in *.Fab
                if frect.x1 is not None:
                    extra_ffab_lines.append(self.cross_module(m, frect, ffab))
                else:
                    extra_ffab_lines.append(None)
                if brect.x1 is not None:
                    extra_bfab_lines.append(self.cross_module(m, brect, bfab))
                else:
                    extra_bfab_lines.append(None)
        # Remmember the data used to undo it
        self.extra_ffab_lines = extra_ffab_lines
        self.extra_bfab_lines = extra_bfab_lines

    def uncross_modules(self, board, comps_hash):
        """ Undo the crosses in *.Fab layer """
        if comps_hash is None or not GS.global_cross_footprints_for_dnp:
            return
        # Undo the drawings
        for m in GS.get_modules_board(board):
            ref = m.GetReference()
            c = comps_hash.get(ref, None)
            if c and c.included and not c.fitted:
                restore = self.extra_ffab_lines.pop(0)
                if restore:
                    for line in restore:
                        m.Remove(line)
                restore = self.extra_bfab_lines.pop(0)
                if restore:
                    for line in restore:
                        m.Remove(line)

    def detect_solder_paste(self, board):
        """ Detects if the top and/or bottom layer has solder paste """
        fpaste = board.GetLayerID('F.Paste')
        bpaste = board.GetLayerID('B.Paste')
        top = bottom = False
        for m in GS.get_modules_board(board):
            for p in m.Pads():
                pad_layers = p.GetLayerSet()
                if not top and fpaste in pad_layers.Seq():
                    top = True
                if not bottom and bpaste in pad_layers.Seq():
                    bottom = True
                if top and bottom:
                    return top, bottom
        return top, bottom

    def remove_paste_and_glue(self, board, comps_hash):
        """ Remove from solder paste layers the filtered components. """
        if comps_hash is None or not (GS.global_remove_solder_paste_for_dnp or GS.global_remove_adhesive_for_dnp):
            return
        exclude = LSET()
        fpaste = board.GetLayerID('F.Paste')
        bpaste = board.GetLayerID('B.Paste')
        exclude.addLayer(fpaste)
        exclude.addLayer(bpaste)
        old_layers = []
        fadhes = board.GetLayerID('F.Adhes')
        badhes = board.GetLayerID('B.Adhes')
        old_fadhes = []
        old_badhes = []
        rescue = board.GetLayerID(GS.work_layer)
        fmask = board.GetLayerID('F.Mask')
        bmask = board.GetLayerID('B.Mask')
        for m in GS.get_modules_board(board):
            ref = m.GetReference()
            c = comps_hash.get(ref, None)
            if c and c.included and not c.fitted:
                # Remove all pads from *.Paste
                if GS.global_remove_solder_paste_for_dnp:
                    old_c_layers = []
                    for p in m.Pads():
                        pad_layers = p.GetLayerSet()
                        is_front = fpaste in pad_layers.Seq()
                        old_c_layers.append(pad_layers.FmtHex())
                        pad_layers.removeLayerSet(exclude)
                        if len(pad_layers.Seq()) == 0:
                            # No layers at all. Ridiculous, but happends.
                            # At least add an F.Mask
                            pad_layers.addLayer(fmask if is_front else bmask)
                            logger.warning(W_WRONGPASTE+'Pad with solder paste, but no copper or solder mask aperture in '+ref)
                        p.SetLayerSet(pad_layers)
                    old_layers.append(old_c_layers)
                # Remove any graphical item in the *.Adhes layers
                if GS.global_remove_adhesive_for_dnp:
                    for gi in m.GraphicalItems():
                        l_gi = gi.GetLayer()
                        if l_gi == fadhes:
                            gi.SetLayer(rescue)
                            old_fadhes.append(gi)
                        if l_gi == badhes:
                            gi.SetLayer(rescue)
                            old_badhes.append(gi)
        # Store the data to undo the above actions
        self.old_layers = old_layers
        self.old_fadhes = old_fadhes
        self.old_badhes = old_badhes
        self.fadhes = fadhes
        self.badhes = badhes
        return exclude

    def restore_paste_and_glue(self, board, comps_hash):
        if comps_hash is None:
            return
        if GS.global_remove_solder_paste_for_dnp:
            for m in GS.get_modules_board(board):
                ref = m.GetReference()
                c = comps_hash.get(ref, None)
                if c and c.included and not c.fitted:
                    restore = self.old_layers.pop(0)
                    for p in m.Pads():
                        pad_layers = p.GetLayerSet()
                        res = restore.pop(0)
                        pad_layers.ParseHex(res, len(res))
                        p.SetLayerSet(pad_layers)
        if GS.global_remove_adhesive_for_dnp:
            for gi in self.old_fadhes:
                gi.SetLayer(self.fadhes)
            for gi in self.old_badhes:
                gi.SetLayer(self.badhes)

    def remove_fab(self, board, comps_hash):
        """ Remove from Fab the excluded components. """
        if comps_hash is None:
            return
        ffab = board.GetLayerID('F.Fab')
        bfab = board.GetLayerID('B.Fab')
        old_ffab = []
        old_bfab = []
        rescue = board.GetLayerID(GS.work_layer)
        for m in GS.get_modules_board(board):
            ref = m.GetReference()
            c = comps_hash.get(ref, None)
            if c is not None and not c.included:
                # Remove any graphical item in the *.Fab layers
                for gi in m.GraphicalItems():
                    l_gi = gi.GetLayer()
                    if l_gi == ffab:
                        gi.SetLayer(rescue)
                        old_ffab.append(gi)
                    if l_gi == bfab:
                        gi.SetLayer(rescue)
                        old_bfab.append(gi)
        # Store the data to undo the above actions
        self.old_ffab = old_ffab
        self.old_bfab = old_bfab
        self.ffab = ffab
        self.bfab = bfab

    def restore_fab(self, board, comps_hash):
        if comps_hash is None:
            return
        for gi in self.old_ffab:
            gi.SetLayer(self.ffab)
        for gi in self.old_bfab:
            gi.SetLayer(self.bfab)

    def replace_3D_models(self, models, new_model, c):
        """ Changes the 3D model using a provided model.
            Stores changes in self.undo_3d_models_rep """
        logger.debug('Changing 3D models for '+c.ref)
        # Get the model references
        models_l = []
        while not models.empty():
            models_l.append(models.pop())
        # Check if we have more than one model
        c_models = len(models_l)
        if c_models > 1:
            new_model = new_model.split(',')
            c_replace = len(new_model)
            if c_models != c_replace:
                raise KiPlotConfigurationError('Found {} models in component {}, but {} replacements provided'.
                                               format(c_models, c, c_replace))
        else:
            new_model = [new_model]
        # Change the models
        replaced = []
        for i, m3d in enumerate(models_l):
            replaced.append(m3d.m_Filename)
            m3d.m_Filename = new_model[i]
        self.undo_3d_models_rep[c.ref] = replaced
        # Push the models back
        for model in models_l:
            models.push_front(model)

    def undo_3d_models_rename(self, board):
        """ Restores the file name for any renamed 3D module """
        for m in GS.get_modules_board(board):
            # Get the model references
            models = m.Models()
            models_l = []
            while not models.empty():
                models_l.append(models.pop())
            # Fix any changed path
            replaced = self.undo_3d_models_rep.get(m.GetReference())
            for i, m3d in enumerate(models_l):
                if m3d.m_Filename in self.undo_3d_models:
                    m3d.m_Filename = self.undo_3d_models[m3d.m_Filename]
                if replaced:
                    m3d.m_Filename = replaced[i]
            # Push the models back
            for model in models_l:
                models.push_front(model)
        # Reset the list of changes
        self.undo_3d_models = {}
        self.undo_3d_models_rep = {}

    def remove_3D_models(self, board, comps_hash):
        """ Removes 3D models for excluded or not fitted components.
            Applies the global_field_3D_model model rename """
        if not comps_hash:
            return
        # Remove the 3D models for not fitted components
        rem_models = []
        for m in GS.get_modules_board(board):
            ref = m.GetReference()
            c = comps_hash.get(ref, None)
            if c:
                # The filter/variant knows about this component
                models = m.Models()
                if c.included and not c.fitted:
                    # Not fitted, remove the 3D model
                    rem_m_models = []
                    while not models.empty():
                        rem_m_models.append(models.pop())
                    rem_models.append(rem_m_models)
                else:
                    # Fitted
                    new_model = c.get_field_value(GS.global_field_3D_model)
                    if new_model:
                        # We will change the 3D model
                        self.replace_3D_models(models, new_model, c)
        self.rem_models = rem_models

    def restore_3D_models(self, board, comps_hash):
        """ Restore the removed 3D models.
            Restores the renamed models. """
        self.undo_3d_models_rename(board)
        if not comps_hash:
            return
        # Undo the removing
        for m in GS.get_modules_board(board):
            ref = m.GetReference()
            c = comps_hash.get(ref, None)
            if c and c.included and not c.fitted:
                models = m.Models()
                restore = self.rem_models.pop(0)
                for model in restore:
                    models.push_front(model)

    def apply_list_of_3D_models(self, enable, slots, m, var):
        # Disable the unused models adding bogus text to the end
        slots = [int(v) for v in slots if v]
        models = m.Models()
        m_objs = []
        # Extract the models, we get a copy
        while not models.empty():
            m_objs.insert(0, models.pop())
        for i, m3d in enumerate(m_objs):
            if self.extra_debug:
                logger.debug('- {} {} {} {}'.format(var, i+1, i+1 in slots, m3d.m_Filename))
            if i+1 not in slots:
                if enable:
                    # Revert the added text
                    m3d.m_Filename = m3d.m_Filename[:-self.len_disable]
                else:
                    # Not used, add text to make their name invalid
                    m3d.m_Filename += DISABLE_3D_MODEL_TEXT
            # Push it back to the module
            models.push_back(m3d)

    def apply_3D_variant_aspect(self, board, enable=False):
        """ Disable/Enable the 3D models that aren't for this variant.
            This mechanism uses the MTEXT attributes. """
        # The magic text is %variant:slot1,slot2...%
        field_regex = re.compile(r'\%([^:]+):([\d,]*)\%')     # Generic (by name)
        field_regex_sp = re.compile(r'\$([^:]*):([\d,]*)\$')  # Variant specific
        self.extra_debug = extra_debug = GS.debug_level > 3
        if extra_debug:
            logger.debug("{} 3D models that aren't for this variant".format('Enable' if enable else 'Disable'))
        self.len_disable = len(DISABLE_3D_MODEL_TEXT)
        variant_name = self.variant.name if self.variant else 'None'
        for m in GS.get_modules_board(board):
            if extra_debug:
                logger.debug("Processing module " + m.GetReference())
            default = None
            matched = False
            # Look for text objects
            for gi in m.GraphicalItems():
                if gi.GetClass() == 'MTEXT':
                    # Check if the text matches the magic style
                    text = gi.GetText().strip()
                    match = field_regex.match(text)
                    if match:
                        # Check if this is for the current variant
                        var = match.group(1)
                        slots = match.group(2).split(',') if match.group(2) else []
                        # Do the match
                        if var == '_default_':
                            default = slots
                            if self.extra_debug:
                                logger.debug('- Found defaults: {}'.format(slots))
                        else:
                            matched = var == variant_name
                        if matched:
                            self.apply_list_of_3D_models(enable, slots, m, var)
                            break
                    else:
                        # Try with the variant specific pattern
                        match = field_regex_sp.match(text)
                        if match:
                            var = match.group(1)
                            slots = match.group(2).split(',') if match.group(2) else []
                            # Do the match
                            matched = self.variant.matches_variant(var)
                            if matched:
                                self.apply_list_of_3D_models(enable, slots, m, var)
                                break
            if not matched and default is not None:
                self.apply_list_of_3D_models(enable, slots, m, '_default_')

    def create_3D_highlight_file(self):
        if self._highlight_3D_file:
            return
        with NamedTemporaryFile(mode='w', suffix='.wrl', delete=False) as f:
            self._highlight_3D_file = f.name
            logger.debug('Creating temporal highlight file '+f.name)
            f.write(HIGHLIGHT_3D_WRL)

    def get_crtyd_bbox(self, board, m):
        fcrtyd = board.GetLayerID('F.CrtYd')
        bcrtyd = board.GetLayerID('B.CrtYd')
        bbox = Rect()
        for gi in m.GraphicalItems():
            if gi.GetClass() == 'MGRAPHIC':
                l_gi = gi.GetLayer()
                if l_gi == fcrtyd or l_gi == bcrtyd:
                    bbox.Union(gi.GetBoundingBox().getWxRect())
        return bbox

    def highlight_3D_models(self, board, highlight):
        if not highlight:
            return
        self.create_3D_highlight_file()
        # TODO: Adjust? Configure?
        z = (100.0 if self.highlight_on_top else 0.1)/2.54
        for m in GS.get_modules_board(board):
            ref = m.GetReference()
            if ref not in highlight:
                continue
            models = m.Models()
            m_pos = m.GetPosition()
            rot = m.GetOrientationDegrees()
            # Measure the courtyard
            bbox = self.get_crtyd_bbox(board, m)
            if bbox.x1 is not None:
                # Use the courtyard as bbox
                w = bbox.x2-bbox.x1
                h = bbox.y2-bbox.y1
                m_cen = wxPoint((bbox.x2+bbox.x1)/2, (bbox.y2+bbox.y1)/2)
            else:
                # No courtyard, ask KiCad
                # This will include things like text
                bbox = m.GetBoundingBox()
                w = bbox.GetWidth()
                h = bbox.GetHeight()
                m_cen = m.GetCenter()
                logger.warning(W_NOCRTYD+"Missing courtyard for `{}`".format(ref))
            # Compute the offset
            off_x = m_cen.x - m_pos.x
            off_y = m_cen.y - m_pos.y
            rrot = math.radians(rot)
            # KiCad coordinates are inverted in the Y axis
            off_y = -off_y
            # Apply the component rotation
            off_xp = off_x*math.cos(rrot)+off_y*math.sin(rrot)
            off_yp = -off_x*math.sin(rrot)+off_y*math.cos(rrot)
            # Create a new 3D model for the highlight
            hl = FP_3DMODEL()
            hl.m_Scale.x = (ToMM(w)+self.highlight_padding)/2.54
            hl.m_Scale.y = (ToMM(h)+self.highlight_padding)/2.54
            hl.m_Scale.z = z
            hl.m_Rotation.z = rot
            hl.m_Offset.x = ToMM(off_xp)
            hl.m_Offset.y = ToMM(off_yp)
            hl.m_Filename = self._highlight_3D_file
            # Add the model
            models.push_back(hl)
        self._highlighted_3D_components = highlight

    def unhighlight_3D_models(self, board):
        if not self._highlighted_3D_components:
            return
        for m in GS.get_modules_board(board):
            if m.GetReference() not in self._highlighted_3D_components:
                continue
            m.Models().pop()
        self._highlighted_3D_components = None

    def filter_pcb_components(self, board, do_3D=False, do_2D=True, highlight=None):
        if not self._comps:
            return False
        self.comps_hash = self.get_refs_hash()
        if do_2D:
            self.cross_modules(board, self.comps_hash)
            self.remove_paste_and_glue(board, self.comps_hash)
            if hasattr(self, 'hide_excluded') and self.hide_excluded:
                self.remove_fab(board, self.comps_hash)
        if do_3D:
            # Disable the models that aren't for this variant
            self.apply_3D_variant_aspect(board)
            # Remove the 3D models for not fitted components (also rename)
            self.remove_3D_models(board, self.comps_hash)
            # Highlight selected components
            self.highlight_3D_models(board, highlight)
        return True

    def unfilter_pcb_components(self, board, do_3D=False, do_2D=True):
        if not self._comps:
            return
        if do_2D:
            self.uncross_modules(board, self.comps_hash)
            self.restore_paste_and_glue(board, self.comps_hash)
            if hasattr(self, 'hide_excluded') and self.hide_excluded:
                self.restore_fab(board, self.comps_hash)
        if do_3D:
            # Undo the removing (also rename)
            self.restore_3D_models(board, self.comps_hash)
            # Re-enable the modules that aren't for this variant
            self.apply_3D_variant_aspect(board, enable=True)
            # Remove the highlight 3D object
            self.unhighlight_3D_models(board)

    def remove_highlight_3D_file(self):
        # Remove the highlight 3D file if it was created
        if self._highlight_3D_file:
            os.remove(self._highlight_3D_file)
            self._highlight_3D_file = None

    def set_title(self, title, sch=False):
        self.old_title = None
        if title:
            if sch:
                self.old_title = GS.sch.get_title()
            else:
                tb = GS.board.GetTitleBlock()
                self.old_title = tb.GetTitle()
            text = self.expand_filename_pcb(title)
            if text[0] == '+':
                text = self.old_title+text[1:]
            if sch:
                self.old_title = GS.sch.set_title(text)
            else:
                tb.SetTitle(text)

    def restore_title(self, sch=False):
        if self.old_title is not None:
            if sch:
                GS.sch.set_title(self.old_title)
            else:
                GS.board.GetTitleBlock().SetTitle(self.old_title)
            self.old_title = None

    def sch_fields_to_pcb(self, comps, board):
        """ Change the module/footprint data according to the filtered fields.
            iBoM can parse it. """
        comps_hash = self.get_refs_hash()
        self.sch_fields_to_pcb_bkp = {}
        first = True
        for m in GS.get_modules_board(board):
            if first:
                has_GetFPIDAsString = hasattr(m, 'GetFPIDAsString')
                first = False
            ref = m.GetReference()
            comp = comps_hash.get(ref, None)
            if comp is not None:
                properties = {f.name: f.value for f in comp.fields}
                old_value = m.GetValue()
                m.SetValue(properties['Value'])
                if GS.ki6:
                    old_properties = m.GetProperties()
                    m.SetProperties(properties)
                    if has_GetFPIDAsString:
                        # Introduced in 6.0.6
                        old_fp = m.GetFPIDAsString()
                        m.SetFPIDAsString(properties['Footprint'])
                        data = (old_value, old_properties, old_fp)
                    else:
                        data = (old_value, old_properties)
                else:
                    data = old_value
                self.sch_fields_to_pcb_bkp[ref] = data
        self._has_GetFPIDAsString = has_GetFPIDAsString

    def restore_sch_fields_to_pcb(self, board):
        """ Undo sch_fields_to_pcb() """
        has_GetFPIDAsString = self._has_GetFPIDAsString
        for m in GS.get_modules_board(board):
            ref = m.GetReference()
            data = self.sch_fields_to_pcb_bkp.get(ref, None)
            if data is not None:
                if GS.ki6:
                    m.SetValue(data[0])
                    m.SetProperties(data[1])
                    if has_GetFPIDAsString:
                        m.SetFPIDAsString(data[2])
                else:
                    m.SetValue(data)

    @staticmethod
    def save_tmp_board(dir=None):
        """ Save the PCB to a temporal file.
            Advantage: all relative paths inside the file remains valid
            Disadvantage: the name of the file gets altered """
        if dir is None:
            dir = GS.pcb_dir
        with NamedTemporaryFile(mode='w', suffix='.kicad_pcb', delete=False, dir=dir) as f:
            fname = f.name
        logger.debug('Storing modified PCB to `{}`'.format(fname))
        GS.board.Save(fname)
        GS.copy_project(fname)
        return fname

    @staticmethod
    def save_tmp_dir_board(id, force_dir=None):
        """ Save the PCB to a temporal dir.
            Disadvantage: all relative paths inside the file becomes useless
            Aadvantage: the name of the file remains the same """
        pcb_dir = mkdtemp(prefix='tmp-kibot-'+id+'-') if force_dir is None else force_dir
        fname = os.path.join(pcb_dir, GS.pcb_basename+'.kicad_pcb')
        logger.debug('Storing modified PCB to `{}`'.format(fname))
        GS.board.Save(fname)
        pro_name = GS.copy_project(fname)
        KiConf.fix_page_layout(pro_name)
        return fname, pcb_dir

    def remove_tmp_board(self, board_name):
        # Remove the temporal PCB
        if board_name != GS.pcb_file:
            # KiCad likes to create project files ...
            for f in glob(board_name.replace('.kicad_pcb', '.*')):
                os.remove(f)

    def solve_kf_filters(self, components):
        """ Solves references to KiBot filters in the list of components to show.
            They are not yet expanded, just solved to filter objects """
        new_list = []
        for c in components:
            c_s = c.strip()
            if c_s.startswith('_kf('):
                # A reference to a KiBot filter
                if c_s[-1] != ')':
                    raise KiPlotConfigurationError('Missing `)` in KiBot filter reference: `{}`'.format(c))
                filter_name = c_s[4:-1].strip().split(';')
                logger.debug('Expanding KiBot filter in list of components: `{}`'.format(filter_name))
                filter = BaseFilter.solve_filter(filter_name, 'show_components')
                if not filter:
                    raise KiPlotConfigurationError('Unknown filter in: `{}`'.format(c))
                new_list.append(filter)
                self._filters_to_expand = True
            else:
                new_list.append(c)
        return new_list

    def expand_kf_components(self, components):
        """ Expands references to filters in show_components """
        if not components:
            return []
        if not self._filters_to_expand:
            return components
        new_list = []
        if self._comps:
            all_comps = self._comps
        else:
            load_sch()
            all_comps = GS.sch.get_components()
            get_board_comps_data(all_comps)
        # Scan the list to show
        for c in components:
            if isinstance(c, str):
                # A reference, just add it
                new_list.append(c)
                continue
            # A filter, add its results
            ext_list = []
            for ac in all_comps:
                if c.filter(ac):
                    ext_list.append(ac.ref)
            new_list += ext_list
        return new_list

    def run(self, output_dir):
        """ Makes the list of components available """
        if not self.dnf_filter and not self.variant and not self.pre_transform:
            return
        load_sch()
        # Get the components list from the schematic
        comps = GS.sch.get_components()
        get_board_comps_data(comps)
        # Apply the filter
        reset_filters(comps)
        comps = apply_pre_transform(comps, self.pre_transform)
        apply_fitted_filter(comps, self.dnf_filter)
        # Apply the variant
        if self.variant:
            # Apply the variant
            comps = self.variant.filter(comps)
        self._comps = comps
