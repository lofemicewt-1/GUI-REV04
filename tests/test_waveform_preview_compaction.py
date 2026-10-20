import unittest
from unittest.mock import patch
from types import SimpleNamespace
from pages.waveform_generator import WaveformGeneratorPage
import tests.test_awp1221_sinc_timing_ui as timing_ui
import pages.awp1221_page as awp

class RepeatExpansionRegressionTests(unittest.TestCase):
 def test_serial_preview_does_not_copy_repeated_sample_arrays(self):
  class OneCycle(list):
   def __mul__(self,count):raise AssertionError('Preview expanded repeat data')
  page=object.__new__(WaveformGeneratorPage)
  value=lambda s:SimpleNamespace(get=lambda:s)
  page.serial_slice_vars=[value('0')]*8
  page.serial_repeat_vars=[value('8')]*8
  page._parse_serial_slice_code=lambda text:0
  page._serial_slice_voltages=lambda code:OneCycle([0.,10.,0.,-2.]*256)
  registers_before=page.build_serial_register_values()
  segments=page._serial_preview_segments()
  self.assertEqual(page.build_serial_register_values(),registers_before)
  self.assertEqual(registers_before,[(register,0x0707) for register in range(0x17,0x1B)])
  self.assertEqual([segment.repeats for segment in segments],[8]*8)
  self.assertEqual(len(segments[0].samples),1024)

class SincRepeatSamplingTests(unittest.TestCase):
 setUp = timing_ui.SincTimingTests.setUp
 draw = timing_ui.SincTimingTests.draw
 def test_256_repeats_sample_only_one_cycle_per_different_segment(self):
  with patch.object(awp,'awp1221_waveform_preview_value',wraps=awp.awp1221_waveform_preview_value) as sample:
   self.draw('serial4',(4,1,1,1),512,256,820,'zh','light')
   self.assertLessEqual(sample.call_count,216)
  self.assertTrue(self.canvas.find_withtag('repeat_omission'))


class CompactLayoutTests(unittest.TestCase):
 def test_repeat_budget_and_true_duration_do_not_expand_samples(self):
  from pages.waveform_preview import PreviewSegment,compact_preview
  samples=[0.,10.,0.,-2.]*256
  layout=compact_preview([PreviewSegment('SIN',samples,10**9,2048)])
  self.assertEqual(len(layout.parts),3)
  self.assertEqual(layout.parts[1].omitted,10**9-2)
  self.assertIs(layout.parts[0].samples,samples)
  self.assertIs(layout.parts[-1].samples,samples)
  self.assertEqual(layout.total,2048*10**9)
  self.assertEqual(layout.position(layout.total),1.)

 def test_adjacent_equal_outputs_merge_but_changes_and_order_survive(self):
  from pages.waveform_preview import PreviewSegment,compact_preview
  a=[0.,10.,0.];b=[0.,20.,0.]
  original=[PreviewSegment('a0',a,8),PreviewSegment('a1',list(a),8),
            PreviewSegment('b',b,1),PreviewSegment('a2',a,8)]
  layout=compact_preview(original)
  visible=[p for p in layout.parts if not p.omitted]
  self.assertEqual([p.label for p in visible],['a0','a1','b','a2','a2'])
  self.assertEqual([p.omitted for p in layout.parts if p.omitted],[14,6])
  self.assertEqual(layout.segment_ends,(0,24,48,51,75))
  self.assertEqual(original[0].repeats,8)
  self.assertEqual(a,[0.,10.,0.])

 def test_different_periods_and_two_repeats_are_not_elided(self):
  from pages.waveform_preview import PreviewSegment,compact_preview
  a=[0.,1.,0.]
  layout=compact_preview([PreviewSegment('fast',a,2,10),PreviewSegment('slow',a,2,20)])
  self.assertEqual(len(layout.parts),4)
  self.assertFalse(any(p.omitted for p in layout.parts))
  self.assertEqual(layout.total,60)
  self.assertEqual(layout.position(20),1/3)

class CompactCanvasTests(unittest.TestCase):
 def setUp(self):
  import tkinter as tk
  from styles.theme import get_colors
  from config.languages import t
  self.root=tk.Tk();self.root.geometry('820x520')
  self.canvas=tk.Canvas(self.root,highlightthickness=0)
  self.canvas.pack(fill='both',expand=True);self.root.update()
  self.addCleanup(self.root.destroy)
  self.colors=get_colors('light');self.tr=lambda key,**kwargs:t('en',key,**kwargs)

 def test_timed_serial_64_cycles_draws_two_cycles_and_retains_total_time(self):
  from pages.awp192x_button_view import draw_timed_waveform
  from pages.waveform_preview import PreviewSegment
  samples=[0.,10.,0.,-2.]*256
  draw_timed_waveform(self.canvas,self.colors,self.tr,
      [PreviewSegment(f'serial{i}',samples,8) for i in range(8)],10)
  curves=self.canvas.find_withtag('wave_curve')
  self.assertEqual(len(curves),2)
  self.assertLessEqual(sum(len(self.canvas.coords(c))//2 for c in curves),2050)
  self.assertEqual(len(self.canvas.find_withtag('repeat_omission')),1)
  summary=self.canvas.find_withtag('wave_duration')[0]
  self.assertIn('655.36 ms',self.canvas.itemcget(summary,'text'))
  cards=self.canvas.find_withtag('segment_duration')
  self.assertEqual(len(cards),8)
  self.assertIn('655.36 ms',self.canvas.itemcget(cards[-1],'text'))

 def test_standard_serial_preview_uses_same_compact_display(self):
  from pages.waveform_preview import PreviewSegment
  page=SimpleNamespace(canvas=self.canvas,colors=self.colors,tr=self.tr,
                       engine=SimpleNamespace(voltage_range=95))
  WaveformGeneratorPage._draw_serial_plot(page,[PreviewSegment('SIN',[0,10,0,-2],256)])
  self.assertEqual(len(self.canvas.find_withtag('wave_curve')),2)
  self.assertEqual(len(self.canvas.find_withtag('repeat_omission')),1)

 def test_awp1018_serial4_omits_equal_slots_but_not_changed_timing(self):
  from pages.awp1018_page import AWP1018NormalModePage
  value=lambda text:SimpleNamespace(get=lambda:text)
  page=SimpleNamespace(colors=self.colors,diff_mode=True,rom_waveform_var=value('SIN'),
   rom_repeat_var=value('1'),tr=self.tr,_diff_serial4_selected=lambda:True,
   _serial4_selected_slices=lambda:[0,1,2,3],
   _diff_slice_preview_cache={i:{'period_us':100} for i in range(4)},
   _generate_diff_preview_segments=lambda:[(f'slice{i}',[0,10,0,-2]) for i in range(4)],
   _serial4_preview_period_us=lambda:400)
  AWP1018NormalModePage._paint_rom_waveform(page,self.canvas,820,520)
  self.assertEqual(len(self.canvas.find_withtag('wave_curve')),2)
  self.assertEqual(len(self.canvas.find_withtag('repeat_omission')),1)
  for i in range(4):page._diff_slice_preview_cache[i]['period_us']=100+i
  AWP1018NormalModePage._paint_rom_waveform(page,self.canvas,820,520)
  self.assertEqual(len(self.canvas.find_withtag('wave_curve')),4)
  self.assertFalse(self.canvas.find_withtag('repeat_omission'))

 def test_awp1018_normal_repeat_is_display_only(self):
  from pages.awp1018_page import AWP1018NormalModePage
  value=lambda text:SimpleNamespace(get=lambda:text)
  page=SimpleNamespace(colors=self.colors,diff_mode=False,rom_waveform_var=value('SIN'),
   rom_repeat_var=value('256'),tr=self.tr,_rom_vpp_voltage=lambda:20,
   _normal_playback_duration_us=lambda:524288)
  AWP1018NormalModePage._paint_rom_waveform(page,self.canvas,820,520)
  self.assertEqual(len(self.canvas.find_withtag('wave_curve')),2)
  self.assertEqual(len(self.canvas.find_withtag('repeat_omission')),1)
