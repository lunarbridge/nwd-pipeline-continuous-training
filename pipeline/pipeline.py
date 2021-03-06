import os

from kfp import dsl, compiler

from . import ops


@dsl.pipeline(name='continuous-training-pipeline')
def continuous_training_pipeline():
    batch_size = 64
    gpu_num = 1
    seed = 42

    eval_prediction_threshold = 0.5

    cpu_limit = '3000m'
    memory_limit = '12G'

    check_preprocess_proceed_task = ops.check_preprocess_proceed_op()
    is_preprocess_required = check_preprocess_proceed_task.outputs['is_preprocess_required']

    with dsl.Condition(is_preprocess_required == 'True', name='preprocess-required'):
        preprocess_required_slide_paths = check_preprocess_proceed_task.outputs['preprocess_required_slide_paths']

        data_preprocess_task = ops.data_preprocess_op(preprocess_required_slide_paths)
        data_preprocess_task.after(check_preprocess_proceed_task)

        train_task = ops.train_op(batch_size=batch_size,
                                 seed=seed)

        train_task.set_gpu_limit(gpu_num)
        train_task.set_cpu_limit(cpu_limit)
        train_task.set_memory_limit(memory_limit)

        train_task.after(data_preprocess_task)
        target_checkpoints_path = train_task.outputs['target_checkpoints_path']

        eval_task = ops.evaluate_op(target_checkpoints_path=target_checkpoints_path,
                                   prediction_threshold=eval_prediction_threshold,
                                   seed=seed)
        
        eval_task.set_gpu_limit(gpu_num)
        eval_task.set_cpu_limit(cpu_limit)
        eval_task.set_memory_limit(memory_limit)
        
        eval_task.after(train_task)

        target_checkpoints_path = eval_task.outputs['target_checkpoints_path']
        checkpoint_metrics_filename = eval_task.outputs['checkpoint_metrics_filename']

        check_deployable_task = ops.check_deployable_op(target_checkpoints_path=target_checkpoints_path,
                                                       checkpoint_metrics_filename=checkpoint_metrics_filename)

        check_deployable_task.after(eval_task)

        deployable_checkpoint_info = check_deployable_task.outputs['deployable_checkpoint_info']
        is_deployable_checkpoint_exist = check_deployable_task.outputs['is_deployable_checkpoint_exist']

        with dsl.Condition(is_deployable_checkpoint_exist == 'True', name='exist'):
            update_deploy_config_task = ops.update_deploy_config_op(
                deployable_checkpoint_info=deployable_checkpoint_info)

            update_deploy_config_task.after(check_deployable_task)

            deploy_task = ops.deploy_op()
            deploy_task.after(update_deploy_config_task)

        with dsl.Condition(is_deployable_checkpoint_exist == 'False', name='not-exist'):
            ops.print_op('deployed checkpoint: previous')

    with dsl.Condition(is_preprocess_required == 'False', name='preprocess-not-required'):
        print_task = ops.print_op('all of the slides are pre-processed')


def create_continuous_training_workflow():
    #ref: https://towardsdatascience.com/build-your-data-pipeline-on-kubernetes-using-kubeflow-pipelines-sdk-and-argo-eef69a80237c
    workflow_dict = compiler.Compiler()._create_workflow(continuous_training_pipeline)
    # ref: https://github.com/argoproj/argo-workflows/issues/4534
    # resolves for: Failed to establish pod watch: timed out waiting for the condition
    workflow_dict['metadata']['namespace'] = 'argo'
    workflow_dict['spec']['serviceAccountName'] = 'default'

    workflow_dict['kind'] = 'WorkflowTemplate'
    del workflow_dict['metadata']['generateName']
    workflow_dict['metadata']['name'] = 'continuous-training-template'

    workflow_dict['spec']['artifactRepositoryRef'] = {}
    workflow_dict['spec']['artifactRepositoryRef']['key'] = 'default'

    # add annotations to get secured value from vault
    # ref: https://itnext.io/argocd-secret-management-with-argocd-vault-plugin-539f104aff05
    workflow_dict['metadata']['annotations']['avp_path'] = 'avp/data/nwd-pipeline-continuous-training'

    curr_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(curr_dir, '../resources/deployed')
    workflow_filename = 'managed-continuous-training-workflow-template.yml'

    if not os.path.exists(output_path):
        os.makedirs(output_path)

    compiler.Compiler._write_workflow(workflow_dict, os.path.join(output_path, workflow_filename))
