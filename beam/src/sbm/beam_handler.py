
import argparse
import json
import logging
import os
import setuptools

import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions
from apache_beam.options.pipeline_options import SetupOptions
from apache_beam.dataframe.convert import to_dataframe
import numpy as np

# Change the name of this...
from generator_beam_handler import GeneratorBeamHandler
from models.beam import BenchmarkGNNParDo
from sbm.sbm_simulator import GenerateStochasticBlockModelWithFeatures
from sbm.utils import sbm_data_to_torchgeo_data, get_kclass_masks

class SampleSbmDoFn(beam.DoFn):

  def __init__(self, nvertex_min, nvertex_max, nedges_min, nedges_max,
               feature_center_distance_max):
    self._nvertex_min = nvertex_min
    self._nvertex_max = nvertex_max
    self._nedges_min = nedges_min
    self._nedges_max = nedges_max
    self._feature_center_distance_max = feature_center_distance_max

  def process(self, sample_id):
    """Sample and save SMB outputs given a configuration filepath.
    """
    # Avoid save_main_session in Pipeline args so the controller doesn't
    # have to import the same libraries as the workers which may be using
    # a custom container. The import will execute once then the sys.modeules
    # will be referenced to further calls.

    # Parameterize me...
    edge_center_distance_min = 1.0
    edge_center_distance_max = 10.0
    feature_dim = 16
    edge_center_distance = 2.0
    edge_feature_dim = 4

    num_vertices = np.random.randint(self._nvertex_min, self._nvertex_max)
    num_edges = np.random.randint(self._nedges_min, self._nedges_max)
    feature_center_distance = np.random.uniform(
      0.0, self._feature_center_distance_max)

    generator_config = {
      'generator_name': 'StochasticBlockModel',
      'num_vertices': num_vertices,
      'num_edges': num_edges,
      'feature_dim': feature_dim,
      'feature_center_distance': feature_center_distance,
      'edge_center_distance': edge_center_distance,
      'edge_feature_dim': 4
    }

    data = GenerateStochasticBlockModelWithFeatures(
      num_vertices=num_vertices,
      num_edges=num_edges,
      pi=np.array([0.25, 0.25, 0.25, 0.25]),
      prop_mat=np.ones((4, 4)) + 9.0 * np.diag([1, 1, 1, 1]),
      feature_center_distance=generator_config['feature_center_distance'],
      feature_dim=generator_config['feature_dim'],
      edge_center_distance=generator_config['edge_center_distance'],
      edge_feature_dim=generator_config['edge_feature_dim']
    )

    yield {'sample_id': sample_id,
           'generator_config': generator_config,
           'data': data}


class WriteSbmDoFn(beam.DoFn):

  def __init__(self, output_path):
    self._output_path = output_path

  def process(self, element):
    sample_id = element['sample_id']
    config = element['generator_config']
    data = element['data']

    text_mime = 'text/plain'
    prefix = '{0:05}'.format(sample_id)
    config_object_name = os.path.join(self._output_path, prefix + '_config.txt')
    with beam.io.filesystems.FileSystems.create(config_object_name, text_mime) as f:
      buf = bytes(json.dumps(config), 'utf-8')
      f.write(buf)
      f.close()

    graph_object_name = os.path.join(self._output_path, prefix + '_graph.gt')
    with beam.io.filesystems.FileSystems.create(graph_object_name) as f:
      data.graph.save(f)
      f.close()

    graph_memberships_object_name = os.path.join(
      self._output_path, prefix + '_graph_memberships.txt')
    with beam.io.filesystems.FileSystems.create(graph_memberships_object_name, text_mime) as f:
      np.savetxt(f, data.graph_memberships)
      f.close()

    node_features_object_name = os.path.join(
      self._output_path, prefix + '_node_features.txt')
    with beam.io.filesystems.FileSystems.create(node_features_object_name, text_mime) as f:
      np.savetxt(f, data.node_features)
      f.close()

    feature_memberships_object_name = os.path.join(
      self._output_path, prefix + '_feature_membership.txt')
    with beam.io.filesystems.FileSystems.create(feature_memberships_object_name, text_mime) as f:
      np.savetxt(f, data.feature_memberships)
      f.close()

    edge_features_object_name = os.path.join(
      self._output_path, prefix + '_edge_features.txt')
    with beam.io.filesystems.FileSystems.create(edge_features_object_name, text_mime) as f:
      for edge_tuple, features in data.edge_features.items():
        buf = bytes('{0},{1},{2}'.format(edge_tuple[0], edge_tuple[1], features), 'utf-8')
        f.write(buf)
      f.close()


class ConvertToTorchGeoDataParDo(beam.DoFn):
  def __init__(self, output_path):
    self._output_path = output_path

  def process(self, element):
    sample_id = element['sample_id']
    sbm_data = element['data']

    out = {
      'sample_id': sample_id,
      'torch_data': None,
      'masks': None,
      'skipped': False
    }

    try:
      torch_data = sbm_data_to_torchgeo_data(sbm_data)
      out['torch_data'] = sbm_data_to_torchgeo_data(sbm_data)
      out['generator_config'] = element['generator_config']

      torchgeo_stats = {
        'nodes': torch_data.num_nodes,
        'edges': torch_data.num_edges,
        'average_node_degree': torch_data.num_edges / torch_data.num_nodes,
        # 'contains_isolated_nodes': torchgeo_data.contains_isolated_nodes(),
        # 'contains_self_loops': torchgeo_data.contains_self_loops(),
        # 'undirected': bool(torchgeo_data.is_undirected())
      }
      stats_object_name = os.path.join(self._output_path, '{0:05}_torch_stats.txt'.format(sample_id))
      with beam.io.filesystems.FileSystems.create(stats_object_name, 'text/plain') as f:
        buf = bytes(json.dumps(torchgeo_stats), 'utf-8')
        f.write(buf)
        f.close()
    except:
      out['skipped'] = True
      print(f'faied convert {sample_id}')
      logging.info(f'Failed to convert sbm_data to torchgeo for sample id {sample_id}')
      yield out

    try:
      out['masks'] = get_kclass_masks(sbm_data)

      masks_object_name = os.path.join(self._output_path, '{0:05}_masks.txt'.format(sample_id))
      with beam.io.filesystems.FileSystems.create(masks_object_name, 'text/plain') as f:
        for mask in out['masks']:
          np.savetxt(f, np.atleast_2d(mask.numpy()), fmt='%i', delimiter=' ')
        f.close()
    except:
      out['skipped'] = True
      print(f'failed masks {sample_id}')
      logging.info(f'Failed to sample masks for sample id {sample_id}')
      yield out

    yield out


class SbmBeamHandler(GeneratorBeamHandler):

  def __init__(self, output_path, nvertex_min, nvertex_max, nedges_min, nedges_max,
               feature_center_distance_max, num_features, num_classes, hidden_channels, epochs):
    self._sample_do_fn = SampleSbmDoFn(nvertex_min, nvertex_max, nedges_min, nedges_max,
                                       feature_center_distance_max)
    self._write_do_fn = WriteSbmDoFn(output_path)
    self._convert_par_do = ConvertToTorchGeoDataParDo(output_path)
    self._benchmark_par_do = BenchmarkGNNParDo(output_path, num_features, num_classes, hidden_channels, epochs)

  def GetSampleDoFn(self):
    return self._sample_do_fn

  def GetWriteDoFn(self):
    return self._write_do_fn

  def GetConvertParDo(self):
    return self._convert_par_do

  def GetBenchmarkParDo(self):
    return self._benchmark_par_do