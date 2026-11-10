"""Two container stages; registration and mutable-reference evaluation are uncached."""
import os
from kfp import compiler, dsl

IMAGE = os.environ.get('KFP_LAB_IMAGE', 'meteo-kfp-lab:v1')

@dsl.container_component
def train(version: dsl.OutputPath(str)):
    return dsl.ContainerSpec(image=IMAGE, command=['python', '/lab/worker.py'],
                             args=['train', '--output', version])

@dsl.container_component
def evaluate(version: str, decision: dsl.OutputPath(str), fail: str = 'false'):
    return dsl.ContainerSpec(image=IMAGE, command=['python', '/lab/worker.py'],
                             args=['evaluate', '--version', version, '--output', decision, '--fail', fail])

@dsl.pipeline(name='meteo-training-evaluation-lab')
def pipeline(fail_evaluation: str = 'false'):
    training = train()
    evaluation = evaluate(version=training.outputs['version'], fail=fail_evaluation)
    evaluation.set_retry(num_retries=1)
    for task in (training, evaluation):
        task.set_caching_options(False)
        task.set_env_variable('MLFLOW_TRACKING_URI', 'http://lab-mlflow:5000')
        task.set_env_variable('MLFLOW_ARTIFACT_ROOT', 'mlflow-artifacts:/')
        task.set_env_variable('TRAINING_IMAGE', IMAGE)
        task.set_cpu_request('250m').set_cpu_limit('2')
        task.set_memory_request('256Mi').set_memory_limit('1Gi')

if __name__ == '__main__':
    compiler.Compiler().compile(pipeline, package_path='/tmp/meteo-kfp-pipeline.yaml')
