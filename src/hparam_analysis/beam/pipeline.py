import argparse
import logging
import os

import apache_beam as beam
import pandas as pd
import gin

from apache_beam.options.pipeline_options import PipelineOptions
from apache_beam.options.pipeline_options import SetupOptions

from ..utils.config_enumeration import enumerate_configs
from ..beam.hparam_eval import HparamBeamHandler


def entry(argv=None):
  parser = argparse.ArgumentParser()

  parser.add_argument('--dataset_path',
                      dest='dataset_path',
                      default='',
                      help=('Location of input data files. '
                            'Default behavior downloads data from web. '
                            'GCP runs will need to input GCS dataset path. '))

  parser.add_argument('--output',
                      dest='output',
                      default='/tmp/graph_configs.json',
                      help='Location to write output files.')

  parser.add_argument('--gin_config',
                      dest='gin_config',
                      default='',
                      help='Location of gin config (/app/configs = /src/configs).')

  args, pipeline_args = parser.parse_known_args(argv)
  logging.info(f'Pipeline Args: {pipeline_args}')
  pipeline_options = PipelineOptions(pipeline_args)
  pipeline_options.view_as(SetupOptions).save_main_session = True

  gin.parse_config_file(args.gin_config)
  hparam_handler = HparamBeamHandler(dataset_path=args.dataset_path)

  with beam.Pipeline(options=pipeline_options) as p:
    dataframe_rows = (
      p | 'Enumerate hyperparameter gridpoints.' >> beam.Create(
        enumerate_configs())
        | 'Test GCN.' >> beam.ParDo(hparam_handler.GetGcnTester())
        | 'Write JSON' >> beam.io.WriteToText(
        os.path.join(args.output, 'results.ndjson'), num_shards=10))
