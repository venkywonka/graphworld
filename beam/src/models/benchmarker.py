import json
import os

from abc import ABC, abstractmethod
import apache_beam as beam
import gin
import pandas as pd

class Benchmarker(ABC):

  def __init__(self):
    self._model_name = ''

  def GetModelName(self):
    return self._model_name

  # Train and test the model.
  # Arguments:
  #   * element: output of the 'Convert to torchgeo' beam stage.
  #   * output_path: where to save logs and data.
  # Returns:
  #   * named dict with keys/vals:
  #      'losses': iterable of loss values over the epochs.
  #      'test_metrics': dict of named test metrics for the benchmark run.
  @abstractmethod
  def Benchmark(self, element):
    del element # unused
    return {'losses': [], 'test_metrics': {}}


class BenchmarkerWrapper(ABC):

  @abstractmethod
  def GetBenchmarker(self):
    return Benchmarker()

  # These two functions would be unnecessary if we were using Python 3.7. See:
  #  - https://github.com/huggingface/transformers/issues/8453
  #  - https://github.com/huggingface/transformers/issues/8212
  @abstractmethod
  def GetBenchmarkerClass(self):
    return Benchmarker

  @abstractmethod
  def GetModelHparams(self):
    return {}


class BenchmarkGNNParDo(beam.DoFn):

  # The commented lines here, and those in process, could be uncommented and
  # replace the alternate code below it, if we were using Python 3.7. See:
  #  - https://github.com/huggingface/transformers/issues/8453
  #  - https://github.com/huggingface/transformers/issues/8212
  def __init__(self, benchmarker_wrappers):
    # self._benchmarkers = [benchmarker_wrapper().GetBenchmarker() for
    #                       benchmarker_wrapper in benchmarker_wrappers]
    self._benchmarker_classes = [benchmarker_wrapper().GetBenchmarkerClass() for
                                 benchmarker_wrapper in benchmarker_wrappers]
    self._model_hparams = [benchmarker_wrapper().GetModelHparams() for
                           benchmarker_wrapper in benchmarker_wrappers]
    # /end alternate code.
    self._output_path = None

  def SetOutputPath(self, output_path):
    self._output_path = output_path

  def process(self, element):
    # for benchmarer in self._benchmarkers:
    for benchmarker_class, model_hparams in zip(self._benchmarker_classes, self._model_hparams):
      sample_id = element['sample_id']
      # benchmarker_out = self._benchmarker.Benchmark(element)
      benchmarker = benchmarker_class(**model_hparams)
      benchmarker_out = benchmarker.Benchmark(element)
      # /end alternate code.

      # Dump benchmark results to file.
      benchmark_result = {
        'sample_id': sample_id,
        'losses': benchmarker_out['losses'],
        'generator_config': element['generator_config']
      }
      benchmark_result.update(benchmarker_out['test_metrics'])
      results_object_name = os.path.join(self._output_path, '{0:05}_results.txt'.format(sample_id))
      with beam.io.filesystems.FileSystems.create(results_object_name, 'text/plain') as f:
        buf = bytes(json.dumps(benchmark_result), 'utf-8')
        f.write(buf)
        f.close()

      # Return benchmark data for next beam stage.
      output_data = benchmarker_out['test_metrics']
      output_data.update(element['generator_config'])
      output_data.update(element['metrics'])
      output_data['model_name'] = benchmarker.GetModelName()
      yield pd.DataFrame(output_data, index=[sample_id])