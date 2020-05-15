# %%
import argparse
import os
import pickle
from time import time

import dill as dill
import numpy as np
import pandas as pd
from scipy.stats import hmean
from sklearn.model_selection import train_test_split
from sklearn.tree import DecisionTreeClassifier
from xgboost import XGBRegressor

from ABRPolicies.ThroughputEstimator import StepEstimator
from BehaviourCloning.ActionCloning import BehavioralCloningIterative, BehavioralCloning, BehavioralCloningDAgger
from BehaviourCloning.ImitationLearning import DAGGERCloner, VIPERCloner
from BehaviourCloning.MLABRPolicy import ABRPolicyClassifierSimple, ABRPolicyValueFunctionLearner, \
    ABRPolicyClassifierAutomatedFeatureEngineering, ABRPolicyClassifierHandFeatureEngineering, ABRPolicyRate
from BehaviourCloning.RewardShapingCloning import GAILPPO, RandomExpertDistillation
from SimulationEnviroment.Rewards import ClassicPerceptualReward
from SimulationEnviroment.SimulatorEnviroment import OfflineStreaming

# %%

PARENT_FOLDER = 'Data'
video_selection_dataframe = PARENT_FOLDER + '/VideoSelectionList/VideoListRecoded.csv'
video_selection_dataframe = pd.read_csv(video_selection_dataframe, index_col=0)
provider_index = video_selection_dataframe.index
provider_index = ['SRF' if t == 'SFR' else t for t in provider_index]
video_selection_dataframe.index = provider_index
ALGORITHM_TYPE = ['Online',
                  'Robust_Rate',
                  'Robust_MPC',
                  'Pensieve_MultiVideo',
                  'Optimal']

parser = argparse.ArgumentParser(description='Run Experiments')
parser.add_argument('provider',
                    help='Select one of the providers shown here %s for analysis' % str(np.unique(provider_index)))
parser.add_argument('algorithm',
                    help='Select one of the algorithms which is in' % ALGORITHM_TYPE)
parser.add_argument('cores_avail', default=1,
                    help='We add a bit of multicoring as we have multiple available')
args = parser.parse_args()

parsed_results_folder = PARENT_FOLDER + '/ParsedResults'
assert args.provider in os.listdir(parsed_results_folder), 'The provider is not parsed'
provider = args.provider
parsed_results_folder = os.path.join(parsed_results_folder, provider)
assert args.algorithm in os.listdir(parsed_results_folder), 'The algorithm is not parsed'
algorithm_type = args.algorithm
parsed_results_folder = os.path.join(parsed_results_folder, algorithm_type)

cores_avail = int(args.cores_avail)

print('Analyzing %s' % provider)
GRANULARITY_AMOUNT_DATA = 5
MAX_AMOUNT_DATA = 2500
MIN_AMOUNT_DATA = 300
EXPERIMENT_FULL_NAME = 'provider_full_evaluation.csv'
MAX_INTERPRETABILITY = 20
IMITATION_EPOCHS = 50

# %%

video_info_files = []
for root, dirs, files in os.walk(PARENT_FOLDER + '/Video_Info/'):
    for name in files:
        if name.endswith('_video_info') and 'Phone' not in root:
            video_info_files.append(os.path.join(root, name))


def find_video_info(path):
    video_id = path.split('/')[-2]
    if '_file_id_' in video_id:
        video_id = video_id.split('_file_id_')[0]
    elif 'bw' in video_id:
        video_id = video_id.split('_bw_')[0]
    else:
        video_id = video_id.split('_epoch_')[0]
    video_id = video_id.replace('video_', '')
    video_info_file = list(filter(lambda path_to_csv: video_id in path_to_csv, video_info_files))
    assert len(video_info_file) == 1, video_info_file
    return video_info_file[0]


trace_files = []
for root, dirs, files in os.walk(PARENT_FOLDER + '/Traces'):
    for name in files:
        trace_files.append(os.path.join(root, name))

print('We have %d traces in the collection' % len(trace_files))


def find_trace_file(path):
    trace_file_id = path.split('/')[-2]
    if '_file_id_' in trace_file_id:
        trace_file_id = trace_file_id.split('_file_id_')[-1]
    elif '_trace_' in trace_file_id:
        trace_file_id = trace_file_id.split('_trace_')[-1]
    else:
        raise ValueError('We dont have a generating file %s' % trace_file_id)
    trace_file = list(filter(lambda path_to_bw_trace: trace_file_id in path_to_bw_trace, trace_files))
    assert len(trace_file) == 1, trace_file
    return trace_file[0]


# %%

reward_function_used = ClassicPerceptualReward()
RANDOM_FIXED_SEED = 42
evaluation_dataframe = {}


def experiment_has_finished(experiment_path):
    return EXPERIMENT_FULL_NAME in os.listdir(experiment_path)


# %%

if __name__ == "__main__":
    try:
        video_selection = video_selection_dataframe.loc[provider.replace('_Phone', '')]
    except:
        video_selection = video_selection_dataframe.loc[provider.replace('_Phone', '').lower()]
    training_videos = video_selection[video_selection['Data Type'] != 'validation']['Video Url'].values
    training_videos = list(training_videos)
    expert_trajectory = os.path.join(parsed_results_folder, 'trajectory_list')
    with open(expert_trajectory, 'rb') as expert_trajectory:
        expert_trajectory = pickle.load(expert_trajectory)
    expert_evaluation = os.path.join(parsed_results_folder, 'evaluation_list')
    with open(expert_evaluation, 'rb') as expert_evaluation:
        expert_evaluation = pickle.load(expert_evaluation)
    expert_evaluation = np.array(expert_evaluation)
    expert_traces = [find_trace_file(f.name + '/') for f in expert_evaluation]
    expert_traces = np.array(expert_traces)
    if '_Phone' in provider:  # Quick fix for the phone data
        expert_videos = [find_video_info(f.name.replace('62085745', '62092214') + '/') for f in expert_evaluation]
    else:
        expert_videos = [find_video_info(f.name + '/') for f in expert_evaluation]
    expert_videos = np.array(expert_videos)
    ######################################################################
    if '_Phone' in provider:  # Quick fix for the phone data
        video_ids = [f.name.split('_file_id_')[0].replace('video_', '').replace('62085745', '62092214') for f in
                     expert_evaluation]
    else:
        video_ids = [f.name.split('_file_id_')[0].replace('video_', '') for f in
                     expert_evaluation]
    training_indices = []
    for id in video_ids:
        mapping = [id in url for url in training_videos]
        assert sum(mapping) <= 1
        if sum(mapping) == 1:
            training_indices.append(True)
        else:
            training_indices.append(False)
    training_indices = np.array(training_indices)
    validation_indices = np.where(1.0 - training_indices)[0].astype(int)
    training_indices = np.where(training_indices)[0].astype(int)
    np.random.seed(RANDOM_FIXED_SEED)

    expert_evaluation_training = expert_evaluation[training_indices]
    expert_trajectory_training = [(expert_evaluation[idx].name,len(expert_evaluation[idx].streaming_session_evaluation)) for idx in training_indices]
    expert_trajectory_training = expert_trajectory.extract_trajectory(expert_trajectory_training)
    expert_traces_training = expert_traces[training_indices]
    expert_videos_training = expert_videos[training_indices]

    expert_evaluation_validation = expert_evaluation[validation_indices]
    expert_trajectory_validation = [(expert_evaluation[idx].name,len(expert_evaluation[idx].streaming_session_evaluation)) for idx in validation_indices]
    expert_trajectory_validation = expert_trajectory.extract_trajectory(
        expert_trajectory_validation)
    expert_traces_validation = expert_traces[validation_indices]
    expert_videos_validation = expert_videos[validation_indices]
    n_training_samples_full = len(expert_trajectory_training.trajectory_list)
    n_full_experiments = len(expert_evaluation_training)
    sample_experiment_float = [len(fr.streaming_session_evaluation) for fr in expert_evaluation_training]
    avg_sample_experiment_int = np.mean(sample_experiment_float)
    max_buffer_s = np.median([f.max_buffer_length_s for f in expert_evaluation_training])
    streaming_enviroment = OfflineStreaming(bw_trace_file=expert_traces_training[0],
                                            video_information_csv_path=expert_videos_training[0],
                                            reward_function=reward_function_used,
                                            max_lookback=10,
                                            max_lookahead=3,
                                            max_switch_allowed=2,
                                            buffer_threshold_ms=max_buffer_s * 1000.)

    assert len(expert_evaluation_training) == len(training_indices)
    len_eval_total = [len(ev_df.streaming_session_evaluation) for ev_df in expert_evaluation_training]
    assert len(expert_trajectory_training.trajectory_list) == sum(len_eval_total), 'Wrong number of comparing instances %d != %d Training' % (
    len(expert_trajectory_training.trajectory_list), sum(len_eval_total))

    len_eval_total = [len(ev_df.streaming_session_evaluation) for ev_df in expert_evaluation_validation]
    assert len(expert_trajectory_validation.trajectory_list) == sum(len_eval_total), 'Wrong number of comparing instances %d != %d Validation' % (
    len(expert_trajectory_training.trajectory_list), sum(len_eval_total))

    print('Setting maximum buffer to %.2f' % (max_buffer_s))
    print('%d Training Samples | %d Validation Samples' % (len(expert_trajectory_training.trajectory_list),
                                                           len(expert_trajectory_validation.trajectory_list)))
    ##############################################################################################################
    # %%
    experiment_folder_template = parsed_results_folder.replace('ParsedResults', 'MethodEvaluationClusteringCost')
    if not os.path.exists(experiment_folder_template):
        os.makedirs(experiment_folder_template)
    ############################################################################################################
    ####
    value_function_learner = ABRPolicyValueFunctionLearner(abr_name='XGB Regressor',
                                                           max_quality_change=2,
                                                           regressor=XGBRegressor())
    ############################################################################################################
    ### Select Algorithms with which to copy directly via BC
    direct_copy_algorithms = []
    direct_copy_algorithms += [ABRPolicyRate(abr_name='Simple Rate Algorithm',
                                             max_quality_change=2,
                                             throughput_predictor=StepEstimator(consider_last_n_steps=5,
                                                                                predictor_function=hmean,
                                                                                robust_estimate=False))]
    for max_leaf_nodes in np.unique(list(np.linspace(5, 100, 9).astype(int)) + [MAX_INTERPRETABILITY]):  ##10 Minutes
        base_classifer = DecisionTreeClassifier(max_leaf_nodes=max_leaf_nodes)
        direct_copy_algorithms += [ABRPolicyClassifierSimple(
            abr_name='No Feature Engineering', max_quality_change=2,
            deterministic=True,
            max_lookahead=3,
            max_lookback=10,
            classifier=base_classifer)]

    for max_leaf_nodes in np.unique(list(np.linspace(5, 35, 9).astype(int)) + [MAX_INTERPRETABILITY]):  ##160 Minutes
        direct_copy_algorithms += [ABRPolicyClassifierHandFeatureEngineering(
            abr_name='Simple Manual Feature Engineering', max_quality_change=2,
            deterministic=True,
            max_lookahead=3,
            max_lookback=10,
            classifier=DecisionTreeClassifier(max_leaf_nodes=max_leaf_nodes),
            feature_complexity='normal')]

    ############################################################################################################
    ### Select Algorithms with which to copy via deep Learning
    deep_learning_cloning_techniques = []
    deep_learning_cloning_techniques += [BehavioralCloningIterative(abr_name='Deep Learning BC',
                                                                    max_quality_change=2,
                                                                    deterministic=True,
                                                                    cloning_epochs=IMITATION_EPOCHS,
                                                                    past_measurement_dimensions=streaming_enviroment.get_past_dims(),
                                                                    future_measurements_dimensions=streaming_enviroment.get_future_dims(),
                                                                    drop_prob=0.0, cores_avail=cores_avail,
                                                                    balanced=False)]
    deep_learning_cloning_techniques += [GAILPPO(abr_name='Generative Adversarial Inverse Learning',
                                                 max_quality_change=2,
                                                 pretrain=True,
                                                 deterministic=True,
                                                 cloning_epochs=IMITATION_EPOCHS,
                                                 adverserial_max_epochs=1,
                                                 pretrain_max_epochs=IMITATION_EPOCHS,
                                                 past_measurement_dimensions=streaming_enviroment.get_past_dims(),
                                                 future_measurements_dimensions=streaming_enviroment.get_future_dims(),
                                                 drop_prob=0.0, cores_avail=cores_avail, balanced=False)]
    deep_learning_cloning_techniques += [RandomExpertDistillation(
        abr_name='RED Learning',
        max_quality_change=2,
        deterministic=True,
        pretrain=True,
        cloning_epochs=IMITATION_EPOCHS,
        model_iterations=IMITATION_EPOCHS // 3,
        past_measurement_dimensions=streaming_enviroment.get_past_dims(),
        future_measurements_dimensions=streaming_enviroment.get_future_dims(),
        drop_prob=0.0, cores_avail=cores_avail,
        rde_distill_epochs=IMITATION_EPOCHS, balanced=False)]

    ############################################################################################################
    ### Select Algorithms with which to copy via deep Learning
    interpretable_classifier = []
    interpretable_classifier += [ABRPolicyClassifierHandFeatureEngineering(
        abr_name='Simple Manual Feature Engineering', max_quality_change=2,
        deterministic=True,
        max_lookahead=3,
        max_lookback=10,
        classifier=DecisionTreeClassifier(max_leaf_nodes=MAX_INTERPRETABILITY),
        feature_complexity='normal')]
    interpretable_classifier += [ABRPolicyClassifierSimple(
        abr_name='No Feature Engineering', max_quality_change=2,
        deterministic=True,
        max_lookahead=3,
        max_lookback=10,
        classifier=DecisionTreeClassifier(max_leaf_nodes=MAX_INTERPRETABILITY))]

    ############################################################################################################
    ### Try Copying via BC for different Settings
    print('Directly copying via BC %d' % len(direct_copy_algorithms))
    for classifier_comparison in direct_copy_algorithms:
        if hasattr(classifier_comparison, 'classifier') and hasattr(classifier_comparison.classifier, 'max_leaf_nodes'):
            experiment_name = '%s BC Full  %d leaf nodes' % (
                classifier_comparison.abr_name, classifier_comparison.classifier.max_leaf_nodes)
        else:
            experiment_name = '%s BC Full' % classifier_comparison.abr_name
        file_name = '_'.join(experiment_name.split(" "))
        experiment_folder_name = os.path.join(experiment_folder_template, file_name)
        if not os.path.exists(experiment_folder_name):
            os.makedirs(experiment_folder_name)
        elif experiment_has_finished(experiment_folder_name):
            evaluation_dataframe = pd.read_csv(os.path.join(
                experiment_folder_name, EXPERIMENT_FULL_NAME), index_col=0).to_dict(orient='list')
            print("We've already done %s " % experiment_name)
            continue
        start_time = time()
        bc_classifier_comparison = BehavioralCloning(classifier=classifier_comparison, validation_split=0.2,
                                                     cores_avail=cores_avail)
        bc_classifier_comparison.clone_from_trajectory(
            expert_trajectory=expert_trajectory_training,
            expert_evaluation=expert_evaluation_training,
            streaming_enviroment=streaming_enviroment,
            trace_list=expert_traces_training,
            video_csv_list=expert_videos_training,
        )
        bc_classifier_comparison_scoring, bc_classifier_comparison_evaluation = bc_classifier_comparison.score(
            expert_evaluation=expert_evaluation_validation,
            expert_trajectory=expert_trajectory_validation,
            streaming_enviroment=streaming_enviroment,
            trace_list=expert_traces_validation,
            video_csv_list=expert_videos_validation, add_data=True,
        )
        bc_classifier_comparison_scoring['Provider'] = [provider]
        bc_classifier_comparison_scoring['Base Classifier'] = [classifier_comparison.abr_name]
        bc_classifier_comparison_scoring['Cloning Method'] = ['BC Full']
        bc_classifier_comparison_scoring['Training Samples'] = [n_training_samples_full]
        leaf_complexity = 0
        if hasattr(classifier_comparison, 'classifier') and hasattr(classifier_comparison.classifier, 'max_leaf_nodes'):
            leaf_complexity = classifier_comparison.classifier.max_leaf_nodes
        bc_classifier_comparison_scoring['Leaf Complexity'] = [leaf_complexity]
        bc_classifier_comparison_scoring['Training Time'] = [time() - start_time]

        for k, v in bc_classifier_comparison_scoring.items():
            if k in evaluation_dataframe:
                evaluation_dataframe[k] += v
            else:
                evaluation_dataframe[k] = v
        print('%s took %.2f s' % (experiment_name, time() - start_time))
        with open(os.path.join(experiment_folder_name, 'classifier'), 'wb') as output_file:
            dill.dump(classifier_comparison, output_file)
        with open(os.path.join(experiment_folder_name, 'evaluation'), 'wb') as output_file:
            dill.dump(bc_classifier_comparison_evaluation, output_file)
        pd.DataFrame(bc_classifier_comparison.policy_history).to_csv(
            os.path.join(experiment_folder_name, 'policy_learning_history.csv'))
        pd.DataFrame(evaluation_dataframe).to_csv(os.path.join(
            experiment_folder_name, EXPERIMENT_FULL_NAME))

    ############################################################################################################
    ### Try Copying via BC for different Settings
    print('Limited Amount of Data copying via BC %d' % len(interpretable_classifier))
    for n_data_points in np.linspace(MIN_AMOUNT_DATA, MAX_AMOUNT_DATA, GRANULARITY_AMOUNT_DATA):
        n_indices = max(int(n_data_points // avg_sample_experiment_int) + 1, 2)
        replace = False
        if n_indices > len(expert_evaluation_training):
            replace = True
        print('Sampling %d experiments for the Limited Data Test' % n_indices)
        training_sample_indices = np.random.choice(np.arange(len(expert_evaluation_training)),
                                                   size=n_indices,
                                                   replace=replace)
        expert_trajectory_training_name = [expert_evaluation_training[idx].name for idx in training_sample_indices]
        expert_trajectory_training_sampled = expert_trajectory_training.extract_trajectory(
            expert_trajectory_training_name)
        expert_evaluation_training_sampled = expert_evaluation_training[training_sample_indices]

        print(
            '%d Training on | %d limit' % (len(expert_trajectory_training_sampled.trajectory_list), n_data_points))
        for classifier_amount_data in interpretable_classifier:
            experiment_name = '%s BC %d points %d leaf nodes' % (classifier_amount_data.abr_name,
                                                                 n_data_points,
                                                                 classifier_amount_data.classifier.max_leaf_nodes)
            file_name = '_'.join(experiment_name.split(" "))
            experiment_folder_name = os.path.join(experiment_folder_template, file_name)
            if not os.path.exists(experiment_folder_name):
                os.makedirs(experiment_folder_name)
            elif experiment_has_finished(experiment_folder_name):
                print("We've already done %s " % experiment_name)
                evaluation_dataframe = pd.read_csv(os.path.join(
                    experiment_folder_name, EXPERIMENT_FULL_NAME), index_col=0).to_dict(orient='list')
                continue
            start_time = time()

            bc_classifier_limited_data = BehavioralCloning(classifier=classifier_amount_data,
                                                           validation_split=0.2, cores_avail=cores_avail)
            ############################################################################

            bc_classifier_limited_data.clone_from_trajectory(
                expert_trajectory=expert_trajectory_training_sampled,
                expert_evaluation=expert_evaluation_training_sampled,
                streaming_enviroment=streaming_enviroment,
                trace_list=expert_traces_training[training_sample_indices],
                video_csv_list=expert_videos_training[training_sample_indices],
            )
            bc_classifier_limited_data_scoring, bc_classifier_limited_data_evaluation = bc_classifier_limited_data.score(
                expert_evaluation=expert_evaluation_validation,
                expert_trajectory=expert_trajectory_validation,
                streaming_enviroment=streaming_enviroment,
                trace_list=expert_traces_validation,
                video_csv_list=expert_videos_validation, add_data=True,
            )
            bc_classifier_limited_data_scoring['Provider'] = [provider]
            bc_classifier_limited_data_scoring['Base Classifier'] = [classifier_amount_data.abr_name]
            bc_classifier_limited_data_scoring['Cloning Method'] = ['BC Limited']
            bc_classifier_limited_data_scoring['Training Samples'] = [n_data_points]
            bc_classifier_limited_data_scoring['Leaf Complexity'] = [
                classifier_amount_data.classifier.max_leaf_nodes]
            bc_classifier_limited_data_scoring['Training Time'] = [time() - start_time]

            for k, v in bc_classifier_limited_data_scoring.items():
                if k in evaluation_dataframe:
                    evaluation_dataframe[k] += v
                else:
                    evaluation_dataframe[k] = v

            print('%s took %.2f s' % (experiment_name, time() - start_time))
            with open(os.path.join(experiment_folder_name, 'classifier'), 'wb') as output_file:
                dill.dump(classifier_amount_data, output_file)
            with open(os.path.join(experiment_folder_name, 'evaluation'), 'wb') as output_file:
                dill.dump(bc_classifier_limited_data_evaluation, output_file)
            pd.DataFrame(bc_classifier_limited_data.policy_history).to_csv(
                os.path.join(experiment_folder_name, 'policy_learning_history.csv'))
            pd.DataFrame(evaluation_dataframe).to_csv(os.path.join(
                experiment_folder_name, EXPERIMENT_FULL_NAME))

    ############################################################################################################
    ### Advanced Cloning Techniques
    print('Fancy Deep Learning Techniques Cloning %d' % len(deep_learning_cloning_techniques))
    for advanced_cloning in deep_learning_cloning_techniques:
        experiment_name = advanced_cloning.abr_name
        file_name = '_'.join(experiment_name.split(" "))
        experiment_folder_name = os.path.join(experiment_folder_template, file_name)
        if not os.path.exists(experiment_folder_name):
            os.makedirs(experiment_folder_name)
        elif experiment_has_finished(experiment_folder_name):
            print("We've already done %s " % experiment_name)
            advanced_cloning.load_model(os.path.join(experiment_folder_name, 'keras_model'))
            evaluation_dataframe = pd.read_csv(os.path.join(
                experiment_folder_name, EXPERIMENT_FULL_NAME), index_col=0).to_dict(orient='list')
            continue

        print(advanced_cloning.abr_name)
        start_time = time()
        advanced_cloning.clone_from_trajectory(
            expert_trajectory=expert_trajectory_training,
            expert_evaluation=expert_evaluation_training,
            streaming_enviroment=streaming_enviroment,
            trace_list=expert_traces_training,
            video_csv_list=expert_videos_training,
        )
        advanced_cloning_scoring, advanced_cloning_evaluation = advanced_cloning.score(
            expert_evaluation=expert_evaluation_validation,
            expert_trajectory=expert_trajectory_validation,
            streaming_enviroment=streaming_enviroment,
            trace_list=expert_traces_validation,
            video_csv_list=expert_videos_validation, add_data=True,
        )
        advanced_cloning_scoring['Provider'] = [provider]
        advanced_cloning_scoring['Base Classifier'] = ['Deep Learning GRU']
        advanced_cloning_scoring['Cloning Method'] = [advanced_cloning.abr_name]
        advanced_cloning_scoring['Training Samples'] = [n_training_samples_full]
        advanced_cloning_scoring['Leaf Complexity'] = ['Inf']
        advanced_cloning_scoring['Training Time'] = [time() - start_time]

        for k, v in advanced_cloning_scoring.items():
            if k in evaluation_dataframe:
                evaluation_dataframe[k] += v
            else:
                evaluation_dataframe[k] = v

        print('%s took %.2f s' % (experiment_name, time() - start_time))
        advanced_cloning.save_model(os.path.join(experiment_folder_name, 'keras_model'))
        with open(os.path.join(experiment_folder_name, 'evaluation'), 'wb') as output_file:
            dill.dump(advanced_cloning_evaluation, output_file)
        pd.DataFrame(advanced_cloning.policy_history).to_csv(
            os.path.join(experiment_folder_name, 'policy_learning_history.csv'))
        pd.DataFrame(evaluation_dataframe).to_csv(os.path.join(
            experiment_folder_name, EXPERIMENT_FULL_NAME))

    ############################################################################################################

    print('DAGGER/VIPER Copying Pipeline for engineered classifier')
    engineered_classifier = [ABRPolicyClassifierHandFeatureEngineering(
        abr_name='Simple Manual Feature Engineering', max_quality_change=2,
        deterministic=True,
        max_lookahead=3,
        max_lookback=10,
        classifier=DecisionTreeClassifier(max_leaf_nodes=MAX_INTERPRETABILITY),
        feature_complexity='normal')]
    engineered_classifier += [ABRPolicyClassifierSimple(
        abr_name='No Feature Engineering', max_quality_change=2,
        deterministic=True,
        max_lookahead=3,
        max_lookback=10,
        classifier=DecisionTreeClassifier(max_leaf_nodes=MAX_INTERPRETABILITY))]

    for eng_cf in engineered_classifier:
        bc_classifier_unlimited_data = BehavioralCloning(classifier=eng_cf,
                                                         validation_split=0.2, cores_avail=cores_avail)
        for expert_algorithm in deep_learning_cloning_techniques:
            imitation_learning_names = ['DAGGER', 'VIPER']
            for imitation_learning_technique, imitation_learning_technique_name in zip([DAGGERCloner, VIPERCloner],
                                                                                       imitation_learning_names):
                cloning_method_name = '%s -> %s' % (expert_algorithm.abr_name, imitation_learning_technique_name)
                experiment_name = '%s -> %s %d leaf nodes' % (cloning_method_name, eng_cf.abr_name,
                                                              eng_cf.classifier.max_leaf_nodes)
                file_name = '_'.join(experiment_name.split(" "))
                experiment_folder_name = os.path.join(experiment_folder_template, file_name)
                if not os.path.exists(experiment_folder_name):
                    os.makedirs(experiment_folder_name)
                elif experiment_has_finished(experiment_folder_name):
                    print("We've already done %s " % experiment_name)
                    evaluation_dataframe = pd.read_csv(os.path.join(
                        experiment_folder_name, EXPERIMENT_FULL_NAME), index_col=0).to_dict(orient='list')
                    continue
                start_time = time()
                imitation_learning_technique = imitation_learning_technique(
                    imitation_learning_technique_name,
                    max_quality_change=2,
                    deterministic=True,
                    training_epochs=IMITATION_EPOCHS,
                    abr_policy_learner=eng_cf,
                    value_function_learner=value_function_learner, cores_avail=cores_avail)
                imitation_learning_technique.clone_from_expert(
                    expert_algorithm=expert_algorithm,
                    streaming_enviroment=streaming_enviroment,
                    trace_list=expert_traces_training,
                    video_csv_list=expert_videos_training,
                    show_progress=True, expert_trajectory=expert_trajectory_training
                )
                imitation_learning_technique_scoring, imitation_learning_technique_evaluation = imitation_learning_technique.score(
                    expert_evaluation=expert_evaluation_validation,
                    expert_trajectory=expert_trajectory_validation,
                    streaming_enviroment=streaming_enviroment,
                    trace_list=expert_traces_validation,
                    video_csv_list=expert_videos_validation, add_data=True,
                )
                imitation_learning_technique_scoring['Provider'] = [provider]
                imitation_learning_technique_scoring['Base Classifier'] = [eng_cf.abr_name]
                imitation_learning_technique_scoring['Cloning Method'] = [cloning_method_name]
                imitation_learning_technique_scoring['Training Samples'] = [n_training_samples_full]
                imitation_learning_technique_scoring['Leaf Complexity'] = [eng_cf.classifier.max_leaf_nodes]
                imitation_learning_technique_scoring['Training Time'] = [time() - start_time]

                for k, v in imitation_learning_technique_scoring.items():
                    if k in evaluation_dataframe:
                        evaluation_dataframe[k] += v
                    else:
                        evaluation_dataframe[k] = v

                print('%s took %.2f s' % (experiment_name, time() - start_time))
                with open(os.path.join(experiment_folder_name, 'classifier'), 'wb') as output_file:
                    dill.dump(eng_cf, output_file)
                with open(os.path.join(experiment_folder_name, 'evaluation'), 'wb') as output_file:
                    dill.dump(imitation_learning_technique_evaluation, output_file)
                pd.DataFrame(imitation_learning_technique.policy_history).to_csv(
                    os.path.join(experiment_folder_name, 'policy_learning_history.csv'))
                pd.DataFrame(evaluation_dataframe).to_csv(os.path.join(
                    experiment_folder_name, EXPERIMENT_FULL_NAME))

    direct_copy_algorithms = []
    base_classifer = DecisionTreeClassifier(max_leaf_nodes=MAX_INTERPRETABILITY)
    direct_copy_algorithms += [ABRPolicyClassifierSimple(
        abr_name='No Feature Engineering Viper', max_quality_change=2,
        deterministic=True,
        max_lookahead=3,
        max_lookback=10,
        classifier=base_classifer)]
    direct_copy_algorithms += [ABRPolicyClassifierHandFeatureEngineering(
        abr_name='Simple Manual Feature Engineering Viper', max_quality_change=2,
        deterministic=True,
        max_lookahead=3,
        max_lookback=10,
        classifier=base_classifer,
        feature_complexity='normal')]
    direct_copy_algorithms += [ABRPolicyClassifierHandFeatureEngineering(
        abr_name='Simple Manual Feature Engineering Viper', max_quality_change=2,
        deterministic=True,
        max_lookahead=3,
        max_lookback=10,
        classifier=DecisionTreeClassifier(max_leaf_nodes=10),
        feature_complexity='normal')]
    ############################################################################################################
    ### Try Copying via BC for different Settings
    print('Directly copying via BC %d' % len(direct_copy_algorithms))
    for classifier_comparison in direct_copy_algorithms:
        if hasattr(classifier_comparison, 'classifier') and hasattr(classifier_comparison.classifier,
                                                                    'max_leaf_nodes'):
            experiment_name = '%s BC Full  %d leaf nodes' % (
                classifier_comparison.abr_name, classifier_comparison.classifier.max_leaf_nodes)
        else:
            experiment_name = '%s BC Full' % classifier_comparison.abr_name
        file_name = '_'.join(experiment_name.split(" "))
        experiment_folder_name = os.path.join(experiment_folder_template, file_name)
        if not os.path.exists(experiment_folder_name):
            os.makedirs(experiment_folder_name)
        elif experiment_has_finished(experiment_folder_name):
            evaluation_dataframe = pd.read_csv(os.path.join(
                experiment_folder_name, EXPERIMENT_FULL_NAME), index_col=0).to_dict(orient='list')
            print("We've already done %s " % experiment_name)
            continue
        start_time = time()
        bc_classifier_comparison = BehavioralCloning(classifier=classifier_comparison, validation_split=0.2,
                                                     cores_avail=cores_avail, weight_samples=True,
                                                     weight_samples_method='Viper')
        bc_classifier_comparison.clone_from_trajectory(
            expert_trajectory=expert_trajectory_training,
            expert_evaluation=expert_evaluation_training,
            streaming_enviroment=streaming_enviroment,
            trace_list=expert_traces_training,
            video_csv_list=expert_videos_training,
        )
        bc_classifier_comparison_scoring, bc_classifier_comparison_evaluation = bc_classifier_comparison.score(
            expert_evaluation=expert_evaluation_validation,
            expert_trajectory=expert_trajectory_validation,
            streaming_enviroment=streaming_enviroment,
            trace_list=expert_traces_validation,
            video_csv_list=expert_videos_validation, add_data=True,
        )
        bc_classifier_comparison_scoring['Provider'] = [provider]
        bc_classifier_comparison_scoring['Base Classifier'] = [classifier_comparison.abr_name]
        bc_classifier_comparison_scoring['Cloning Method'] = ['BC Full']
        bc_classifier_comparison_scoring['Training Samples'] = [n_training_samples_full]
        leaf_complexity = 0
        if hasattr(classifier_comparison, 'classifier') and hasattr(classifier_comparison.classifier,
                                                                    'max_leaf_nodes'):
            leaf_complexity = classifier_comparison.classifier.max_leaf_nodes
        bc_classifier_comparison_scoring['Leaf Complexity'] = [leaf_complexity]
        bc_classifier_comparison_scoring['Training Time'] = [time() - start_time]

        for k, v in bc_classifier_comparison_scoring.items():
            if k in evaluation_dataframe:
                evaluation_dataframe[k] += v
            else:
                evaluation_dataframe[k] = v
        print('%s took %.2f s' % (experiment_name, time() - start_time))
        with open(os.path.join(experiment_folder_name, 'classifier'), 'wb') as output_file:
            dill.dump(classifier_comparison, output_file)
        with open(os.path.join(experiment_folder_name, 'evaluation'), 'wb') as output_file:
            dill.dump(bc_classifier_comparison_evaluation, output_file)
        pd.DataFrame(bc_classifier_comparison.policy_history).to_csv(
            os.path.join(experiment_folder_name, 'policy_learning_history.csv'))
        pd.DataFrame(evaluation_dataframe).to_csv(os.path.join(
            experiment_folder_name, EXPERIMENT_FULL_NAME))

    direct_copy_algorithms = []
    base_classifer = DecisionTreeClassifier(max_leaf_nodes=MAX_INTERPRETABILITY)
    direct_copy_algorithms += [ABRPolicyClassifierSimple(
        abr_name='No Feature Engineering Viper Adapted', max_quality_change=2,
        deterministic=True,
        max_lookahead=3,
        max_lookback=10,
        classifier=base_classifer)]
    direct_copy_algorithms += [ABRPolicyClassifierHandFeatureEngineering(
        abr_name='Simple Manual Feature Engineering Viper Adapted', max_quality_change=2,
        deterministic=True,
        max_lookahead=3,
        max_lookback=10,
        classifier=base_classifer,
        feature_complexity='normal')]
    direct_copy_algorithms += [ABRPolicyClassifierHandFeatureEngineering(
        abr_name='Simple Manual Feature Engineering Viper Adapted', max_quality_change=2,
        deterministic=True,
        max_lookahead=3,
        max_lookback=10,
        classifier=DecisionTreeClassifier(max_leaf_nodes=10),
        feature_complexity='normal')]
    ############################################################################################################
    ### Try Copying via BC for different Settings
    print('Directly copying via BC %d' % len(direct_copy_algorithms))
    for classifier_comparison in direct_copy_algorithms:
        if hasattr(classifier_comparison, 'classifier') and hasattr(classifier_comparison.classifier,
                                                                    'max_leaf_nodes'):
            experiment_name = '%s BC Full  %d leaf nodes' % (
                classifier_comparison.abr_name, classifier_comparison.classifier.max_leaf_nodes)
        else:
            experiment_name = '%s BC Full' % classifier_comparison.abr_name
        file_name = '_'.join(experiment_name.split(" "))
        experiment_folder_name = os.path.join(experiment_folder_template, file_name)
        if not os.path.exists(experiment_folder_name):
            os.makedirs(experiment_folder_name)
        elif experiment_has_finished(experiment_folder_name):
            evaluation_dataframe = pd.read_csv(os.path.join(
                experiment_folder_name, EXPERIMENT_FULL_NAME), index_col=0).to_dict(orient='list')
            print("We've already done %s " % experiment_name)
            continue
        start_time = time()
        bc_classifier_comparison = BehavioralCloning(classifier=classifier_comparison, validation_split=0.2,
                                                     cores_avail=cores_avail, weight_samples=True,
                                                     weight_samples_method='Divergence')
        bc_classifier_comparison.clone_from_trajectory(
            expert_trajectory=expert_trajectory_training,
            expert_evaluation=expert_evaluation_training,
            streaming_enviroment=streaming_enviroment,
            trace_list=expert_traces_training,
            video_csv_list=expert_videos_training,
        )
        bc_classifier_comparison_scoring, bc_classifier_comparison_evaluation = bc_classifier_comparison.score(
            expert_evaluation=expert_evaluation_validation,
            expert_trajectory=expert_trajectory_validation,
            streaming_enviroment=streaming_enviroment,
            trace_list=expert_traces_validation,
            video_csv_list=expert_videos_validation,
        )
        bc_classifier_comparison_scoring['Provider'] = [provider]
        bc_classifier_comparison_scoring['Base Classifier'] = [classifier_comparison.abr_name]
        bc_classifier_comparison_scoring['Cloning Method'] = ['BC Full']
        bc_classifier_comparison_scoring['Training Samples'] = [n_training_samples_full]
        leaf_complexity = 0
        if hasattr(classifier_comparison, 'classifier') and hasattr(classifier_comparison.classifier,
                                                                    'max_leaf_nodes'):
            leaf_complexity = classifier_comparison.classifier.max_leaf_nodes
        bc_classifier_comparison_scoring['Leaf Complexity'] = [leaf_complexity]
        bc_classifier_comparison_scoring['Training Time'] = [time() - start_time]

        for k, v in bc_classifier_comparison_scoring.items():
            if k in evaluation_dataframe:
                evaluation_dataframe[k] += v
            else:
                evaluation_dataframe[k] = v
        print('%s took %.2f s' % (experiment_name, time() - start_time))
        with open(os.path.join(experiment_folder_name, 'classifier'), 'wb') as output_file:
            dill.dump(classifier_comparison, output_file)
        with open(os.path.join(experiment_folder_name, 'evaluation'), 'wb') as output_file:
            dill.dump(bc_classifier_comparison_evaluation, output_file)
        pd.DataFrame(bc_classifier_comparison.policy_history).to_csv(
            os.path.join(experiment_folder_name, 'policy_learning_history.csv'))
        pd.DataFrame(evaluation_dataframe).to_csv(os.path.join(
            experiment_folder_name, EXPERIMENT_FULL_NAME))

    print('DAGGER/VIPER Copying Pipeline for engineered classifier')
    engineered_classifier = [ABRPolicyClassifierHandFeatureEngineering(
        abr_name='Simple Manual Feature Engineering', max_quality_change=2,
        deterministic=True,
        max_lookahead=3,
        max_lookback=10,
        classifier=DecisionTreeClassifier(max_leaf_nodes=10),
        feature_complexity='normal')]

    for eng_cf in engineered_classifier:
        bc_classifier_unlimited_data = BehavioralCloning(classifier=eng_cf,
                                                         validation_split=0.2, cores_avail=cores_avail)
        for expert_algorithm in deep_learning_cloning_techniques:
            imitation_learning_names = ['DAGGER', 'VIPER']
            for imitation_learning_technique, imitation_learning_technique_name in zip([DAGGERCloner, VIPERCloner],
                                                                                       imitation_learning_names):
                cloning_method_name = '%s -> %s' % (expert_algorithm.abr_name, imitation_learning_technique_name)
                experiment_name = '%s -> %s %d leaf nodes' % (cloning_method_name, eng_cf.abr_name,
                                                              eng_cf.classifier.max_leaf_nodes)
                file_name = '_'.join(experiment_name.split(" "))
                experiment_folder_name = os.path.join(experiment_folder_template, file_name)
                if not os.path.exists(experiment_folder_name):
                    os.makedirs(experiment_folder_name)
                elif experiment_has_finished(experiment_folder_name):
                    print("We've already done %s " % experiment_name)
                    evaluation_dataframe = pd.read_csv(os.path.join(
                        experiment_folder_name, EXPERIMENT_FULL_NAME), index_col=0).to_dict(orient='list')
                    continue
                start_time = time()
                imitation_learning_technique = imitation_learning_technique(
                    imitation_learning_technique_name,
                    max_quality_change=2,
                    deterministic=True,
                    training_epochs=IMITATION_EPOCHS,
                    abr_policy_learner=eng_cf,
                    value_function_learner=value_function_learner, cores_avail=cores_avail)
                imitation_learning_technique.clone_from_expert(
                    expert_algorithm=expert_algorithm,
                    streaming_enviroment=streaming_enviroment,
                    trace_list=expert_traces_training,
                    video_csv_list=expert_videos_training,
                    show_progress=True, expert_trajectory=expert_trajectory_training
                )
                imitation_learning_technique_scoring, imitation_learning_technique_evaluation = imitation_learning_technique.score(
                    expert_evaluation=expert_evaluation_validation,
                    expert_trajectory=expert_trajectory_validation,
                    streaming_enviroment=streaming_enviroment,
                    trace_list=expert_traces_validation,
                    video_csv_list=expert_videos_validation, add_data=True,
                )
                imitation_learning_technique_scoring['Provider'] = [provider]
                imitation_learning_technique_scoring['Base Classifier'] = [eng_cf.abr_name]
                imitation_learning_technique_scoring['Cloning Method'] = [cloning_method_name]
                imitation_learning_technique_scoring['Training Samples'] = [n_training_samples_full]
                imitation_learning_technique_scoring['Leaf Complexity'] = [eng_cf.classifier.max_leaf_nodes]
                imitation_learning_technique_scoring['Training Time'] = [time() - start_time]

                for k, v in imitation_learning_technique_scoring.items():
                    if k in evaluation_dataframe:
                        evaluation_dataframe[k] += v
                    else:
                        evaluation_dataframe[k] = v

                print('%s took %.2f s' % (experiment_name, time() - start_time))
                with open(os.path.join(experiment_folder_name, 'classifier'), 'wb') as output_file:
                    dill.dump(eng_cf, output_file)
                with open(os.path.join(experiment_folder_name, 'evaluation'), 'wb') as output_file:
                    dill.dump(imitation_learning_technique_evaluation, output_file)
                pd.DataFrame(imitation_learning_technique.policy_history).to_csv(
                    os.path.join(experiment_folder_name, 'policy_learning_history.csv'))
                pd.DataFrame(evaluation_dataframe).to_csv(os.path.join(
                    experiment_folder_name, EXPERIMENT_FULL_NAME))

    interpretable_classifier = []
    interpretable_classifier += [ABRPolicyClassifierAutomatedFeatureEngineering(
        abr_name='Automated Feature Engineering Short', max_quality_change=2,
        deterministic=True,
        max_lookahead=3,
        max_lookback=10,
        classifier=DecisionTreeClassifier(max_leaf_nodes=MAX_INTERPRETABILITY),
        time_budget_s=50)]

    interpretable_classifier += [ABRPolicyClassifierAutomatedFeatureEngineering(
        abr_name='Automated Feature Engineering Long', max_quality_change=2,
        deterministic=True,
        max_lookahead=3,
        max_lookback=10,
        classifier=DecisionTreeClassifier(max_leaf_nodes=MAX_INTERPRETABILITY),
        time_budget_s=100)]

    interpretable_classifier += [ABRPolicyClassifierAutomatedFeatureEngineering(
        abr_name='Automated Feature Engineering Very Long', max_quality_change=2,
        deterministic=True,
        max_lookahead=3,
        max_lookback=10,
        classifier=DecisionTreeClassifier(max_leaf_nodes=MAX_INTERPRETABILITY),
        time_budget_s=200)]
    ############################################################################################################
    ### Try Copying via BC for different Settings
    print('Directly copying via BC %d' % len(direct_copy_algorithms))
    for classifier_comparison in interpretable_classifier:
        if hasattr(classifier_comparison, 'classifier') and hasattr(classifier_comparison.classifier,
                                                                    'max_leaf_nodes'):
            experiment_name = '%s BC Full  %d leaf nodes' % (
                classifier_comparison.abr_name, classifier_comparison.classifier.max_leaf_nodes)
        else:
            experiment_name = '%s BC Full' % classifier_comparison.abr_name
        file_name = '_'.join(experiment_name.split(" "))
        experiment_folder_name = os.path.join(experiment_folder_template, file_name)
        if not os.path.exists(experiment_folder_name):
            os.makedirs(experiment_folder_name)
        elif experiment_has_finished(experiment_folder_name):
            evaluation_dataframe = pd.read_csv(os.path.join(
                experiment_folder_name, EXPERIMENT_FULL_NAME), index_col=0).to_dict(orient='list')
            print("We've already done %s " % experiment_name)
            continue
        start_time = time()
        bc_classifier_comparison = BehavioralCloning(classifier=classifier_comparison, validation_split=0.2,
                                                     cores_avail=cores_avail)
        bc_classifier_comparison.clone_from_trajectory(
            expert_trajectory=expert_trajectory_training,
            expert_evaluation=expert_evaluation_training,
            streaming_enviroment=streaming_enviroment,
            trace_list=expert_traces_training,
            video_csv_list=expert_videos_training,
        )
        bc_classifier_comparison_scoring, bc_classifier_comparison_evaluation = bc_classifier_comparison.score(
            expert_evaluation=expert_evaluation_validation,
            expert_trajectory=expert_trajectory_validation,
            streaming_enviroment=streaming_enviroment,
            trace_list=expert_traces_validation,
            video_csv_list=expert_videos_validation, add_data=True,
        )
        bc_classifier_comparison_scoring['Provider'] = [provider]
        bc_classifier_comparison_scoring['Base Classifier'] = [classifier_comparison.abr_name]
        bc_classifier_comparison_scoring['Cloning Method'] = ['BC Full']
        bc_classifier_comparison_scoring['Training Samples'] = [n_training_samples_full]
        leaf_complexity = 0
        if hasattr(classifier_comparison, 'classifier') and hasattr(classifier_comparison.classifier,
                                                                    'max_leaf_nodes'):
            leaf_complexity = classifier_comparison.classifier.max_leaf_nodes
        bc_classifier_comparison_scoring['Leaf Complexity'] = [leaf_complexity]
        bc_classifier_comparison_scoring['Training Time'] = [time() - start_time]

        for k, v in bc_classifier_comparison_scoring.items():
            if k in evaluation_dataframe:
                evaluation_dataframe[k] += v
            else:
                evaluation_dataframe[k] = v
        print('%s took %.2f s' % (experiment_name, time() - start_time))
        with open(os.path.join(experiment_folder_name, 'classifier'), 'wb') as output_file:
            dill.dump(classifier_comparison, output_file)
        with open(os.path.join(experiment_folder_name, 'evaluation'), 'wb') as output_file:
            dill.dump(bc_classifier_comparison_evaluation, output_file)
        pd.DataFrame(bc_classifier_comparison.policy_history).to_csv(
            os.path.join(experiment_folder_name, 'policy_learning_history.csv'))
        pd.DataFrame(evaluation_dataframe).to_csv(os.path.join(
            experiment_folder_name, EXPERIMENT_FULL_NAME))

    ############################################################################################################
    ### Try Copying via BC for different Settings
    print('Limited Amount of Data copying via BC %d' % len(interpretable_classifier))
    for n_data_points in np.linspace(MIN_AMOUNT_DATA, MAX_AMOUNT_DATA, GRANULARITY_AMOUNT_DATA):
        n_indices = max(int(n_data_points // avg_sample_experiment_int) + 1, 2)
        replace = False
        if n_indices > len(expert_evaluation_training):
            replace = True
        print('Sampling %d experiments for the Limited Data Test' % n_indices)
        training_sample_indices = np.random.choice(np.arange(len(expert_evaluation_training)),
                                                   size=n_indices,
                                                   replace=replace)
        expert_trajectory_training_name = [expert_evaluation_training[idx].name for idx in training_sample_indices]
        expert_trajectory_training_sampled = expert_trajectory_training.extract_trajectory(
            expert_trajectory_training_name)
        expert_evaluation_training_sampled = expert_evaluation_training[training_sample_indices]

        print(
            '%d Training on | %d limit' % (len(expert_trajectory_training_sampled.trajectory_list), n_data_points))
        for classifier_amount_data in interpretable_classifier:
            experiment_name = '%s BC %d points %d leaf nodes' % (classifier_amount_data.abr_name,
                                                                 n_data_points,
                                                                 classifier_amount_data.classifier.max_leaf_nodes)
            file_name = '_'.join(experiment_name.split(" "))
            experiment_folder_name = os.path.join(experiment_folder_template, file_name)
            if not os.path.exists(experiment_folder_name):
                os.makedirs(experiment_folder_name)
            elif experiment_has_finished(experiment_folder_name):
                print("We've already done %s " % experiment_name)
                evaluation_dataframe = pd.read_csv(os.path.join(
                    experiment_folder_name, EXPERIMENT_FULL_NAME), index_col=0).to_dict(orient='list')
                continue
            start_time = time()

            bc_classifier_limited_data = BehavioralCloning(classifier=classifier_amount_data,
                                                           validation_split=0.2, cores_avail=cores_avail)
            ############################################################################

            bc_classifier_limited_data.clone_from_trajectory(
                expert_trajectory=expert_trajectory_training_sampled,
                expert_evaluation=expert_evaluation_training_sampled,
                streaming_enviroment=streaming_enviroment,
                trace_list=expert_traces_training[training_sample_indices],
                video_csv_list=expert_videos_training[training_sample_indices],
            )
            bc_classifier_limited_data_scoring, bc_classifier_limited_data_evaluation = bc_classifier_limited_data.score(
                expert_evaluation=expert_evaluation_validation,
                expert_trajectory=expert_trajectory_validation,
                streaming_enviroment=streaming_enviroment,
                trace_list=expert_traces_validation,
                video_csv_list=expert_videos_validation, add_data=True,
            )
            bc_classifier_limited_data_scoring['Provider'] = [provider]
            bc_classifier_limited_data_scoring['Base Classifier'] = [classifier_amount_data.abr_name]
            bc_classifier_limited_data_scoring['Cloning Method'] = ['BC Limited']
            bc_classifier_limited_data_scoring['Training Samples'] = [n_data_points]
            bc_classifier_limited_data_scoring['Leaf Complexity'] = [
                classifier_amount_data.classifier.max_leaf_nodes]
            bc_classifier_limited_data_scoring['Training Time'] = [time() - start_time]

            for k, v in bc_classifier_limited_data_scoring.items():
                if k in evaluation_dataframe:
                    evaluation_dataframe[k] += v
                else:
                    evaluation_dataframe[k] = v

            print('%s took %.2f s' % (experiment_name, time() - start_time))
            with open(os.path.join(experiment_folder_name, 'classifier'), 'wb') as output_file:
                dill.dump(classifier_amount_data, output_file)
            with open(os.path.join(experiment_folder_name, 'evaluation'), 'wb') as output_file:
                dill.dump(bc_classifier_limited_data_evaluation, output_file)
            pd.DataFrame(bc_classifier_limited_data.policy_history).to_csv(
                os.path.join(experiment_folder_name, 'policy_learning_history.csv'))
            pd.DataFrame(evaluation_dataframe).to_csv(os.path.join(
                experiment_folder_name, EXPERIMENT_FULL_NAME))

    interpretable_classifier = []
    interpretable_classifier += [ABRPolicyClassifierHandFeatureEngineering(
        abr_name='Simple Manual Feature Engineering', max_quality_change=2,
        deterministic=True,
        max_lookahead=3,
        max_lookback=10,
        classifier=DecisionTreeClassifier(max_leaf_nodes=MAX_INTERPRETABILITY,class_weight='balanced'),
        feature_complexity='normal')]
    interpretable_classifier += [ABRPolicyClassifierSimple(
        abr_name='No Feature Engineering', max_quality_change=2,
        deterministic=True,
        max_lookahead=3,
        max_lookback=10,
        classifier=DecisionTreeClassifier(max_leaf_nodes=MAX_INTERPRETABILITY,class_weight='balanced'))]

    ############################################################################################################
    ### Try Copying via BC for different Settings
    print('Directly copying via BC %d' % len(direct_copy_algorithms))
    for classifier_comparison in interpretable_classifier:
        if hasattr(classifier_comparison, 'classifier') and hasattr(classifier_comparison.classifier, 'max_leaf_nodes'):
            experiment_name = '%s BC Full Balanced  %d leaf nodes' % (
                classifier_comparison.abr_name, classifier_comparison.classifier.max_leaf_nodes)
        else:
            experiment_name = '%s BC Full Balanced' % classifier_comparison.abr_name
        file_name = '_'.join(experiment_name.split(" "))
        experiment_folder_name = os.path.join(experiment_folder_template, file_name)
        if not os.path.exists(experiment_folder_name):
            os.makedirs(experiment_folder_name)
        elif experiment_has_finished(
                experiment_folder_name):
            evaluation_dataframe = pd.read_csv(os.path.join(
                experiment_folder_name, EXPERIMENT_FULL_NAME), index_col=0).to_dict(orient='list')
            print("We've already done %s " % experiment_name)
            continue
        start_time = time()
        bc_classifier_comparison = BehavioralCloning(classifier=classifier_comparison, validation_split=0.2,
                                                     cores_avail=cores_avail)
        bc_classifier_comparison.clone_from_trajectory(
            expert_trajectory=expert_trajectory_training,
            expert_evaluation=expert_evaluation_training,
            streaming_enviroment=streaming_enviroment,
            trace_list=expert_traces_training,
            video_csv_list=expert_videos_training,
        )
        bc_classifier_comparison_scoring, bc_classifier_comparison_evaluation = bc_classifier_comparison.score(
            expert_evaluation=expert_evaluation_validation,
            expert_trajectory=expert_trajectory_validation,
            streaming_enviroment=streaming_enviroment,
            trace_list=expert_traces_validation,
            video_csv_list=expert_videos_validation, add_data=True,
        )
        bc_classifier_comparison_scoring['Provider'] = [provider]
        bc_classifier_comparison_scoring['Base Classifier'] = [classifier_comparison.abr_name]
        bc_classifier_comparison_scoring['Cloning Method'] = ['BC Full Balanced']
        bc_classifier_comparison_scoring['Training Samples'] = [n_training_samples_full]
        leaf_complexity = 0
        if hasattr(classifier_comparison, 'classifier') and hasattr(classifier_comparison.classifier, 'max_leaf_nodes'):
            leaf_complexity = classifier_comparison.classifier.max_leaf_nodes
        bc_classifier_comparison_scoring['Leaf Complexity'] = [leaf_complexity]
        bc_classifier_comparison_scoring['Training Time'] = [time() - start_time]

        for k, v in bc_classifier_comparison_scoring.items():
            if k in evaluation_dataframe:
                evaluation_dataframe[k] += v
            else:
                evaluation_dataframe[k] = v
        print('%s took %.2f s' % (experiment_name, time() - start_time))
        with open(os.path.join(experiment_folder_name, 'classifier'), 'wb') as output_file:
            dill.dump(classifier_comparison, output_file)
        with open(os.path.join(experiment_folder_name, 'evaluation'), 'wb') as output_file:
            dill.dump(bc_classifier_comparison_evaluation, output_file)
        pd.DataFrame(bc_classifier_comparison.policy_history).to_csv(
            os.path.join(experiment_folder_name, 'policy_learning_history.csv'))
        pd.DataFrame(evaluation_dataframe).to_csv(os.path.join(
            experiment_folder_name, EXPERIMENT_FULL_NAME))

    print('DAGGER/VIPER Copying Pipeline for engineered classifier')

    for eng_cf in interpretable_classifier:
        bc_classifier_unlimited_data = BehavioralCloning(classifier=eng_cf,
                                                         validation_split=0.2, cores_avail=cores_avail)
        for expert_algorithm in deep_learning_cloning_techniques:
            imitation_learning_names = ['DAGGER', 'VIPER']
            for imitation_learning_technique, imitation_learning_technique_name in zip([DAGGERCloner, VIPERCloner],
                                                                                       imitation_learning_names):
                cloning_method_name = '%s -> %s Balanced ' % (expert_algorithm.abr_name, imitation_learning_technique_name)
                experiment_name = '%s -> %s %d leaf nodes' % (cloning_method_name, eng_cf.abr_name,
                                                              eng_cf.classifier.max_leaf_nodes)
                file_name = '_'.join(experiment_name.split(" "))
                experiment_folder_name = os.path.join(experiment_folder_template, file_name)
                if not os.path.exists(experiment_folder_name):
                    os.makedirs(experiment_folder_name)
                elif experiment_has_finished(experiment_folder_name):
                    print("We've already done %s " % experiment_name)
                    evaluation_dataframe = pd.read_csv(os.path.join(
                        experiment_folder_name, EXPERIMENT_FULL_NAME), index_col=0).to_dict(orient='list')
                    continue
                start_time = time()
                imitation_learning_technique = imitation_learning_technique(
                    imitation_learning_technique_name,
                    max_quality_change=2,
                    deterministic=True,
                    training_epochs=IMITATION_EPOCHS,
                    abr_policy_learner=eng_cf,
                    value_function_learner=value_function_learner, cores_avail=cores_avail)
                imitation_learning_technique.clone_from_expert(
                    expert_algorithm=expert_algorithm,
                    streaming_enviroment=streaming_enviroment,
                    trace_list=expert_traces_training,
                    video_csv_list=expert_videos_training,
                    show_progress=True, expert_trajectory=expert_trajectory_training
                )
                imitation_learning_technique_scoring, imitation_learning_technique_evaluation = imitation_learning_technique.score(
                    expert_evaluation=expert_evaluation_validation,
                    expert_trajectory=expert_trajectory_validation,
                    streaming_enviroment=streaming_enviroment,
                    trace_list=expert_traces_validation,
                    video_csv_list=expert_videos_validation, add_data=True,
                )
                imitation_learning_technique_scoring['Provider'] = [provider]
                imitation_learning_technique_scoring['Base Classifier'] = [eng_cf.abr_name]
                imitation_learning_technique_scoring['Cloning Method'] = [cloning_method_name]
                imitation_learning_technique_scoring['Training Samples'] = [n_training_samples_full]
                imitation_learning_technique_scoring['Leaf Complexity'] = [eng_cf.classifier.max_leaf_nodes]
                imitation_learning_technique_scoring['Training Time'] = [time() - start_time]

                for k, v in imitation_learning_technique_scoring.items():
                    if k in evaluation_dataframe:
                        evaluation_dataframe[k] += v
                    else:
                        evaluation_dataframe[k] = v

                print('%s took %.2f s' % (experiment_name, time() - start_time))
                with open(os.path.join(experiment_folder_name, 'classifier'), 'wb') as output_file:
                    dill.dump(eng_cf, output_file)
                with open(os.path.join(experiment_folder_name, 'evaluation'), 'wb') as output_file:
                    dill.dump(imitation_learning_technique_evaluation, output_file)
                pd.DataFrame(imitation_learning_technique.policy_history).to_csv(
                    os.path.join(experiment_folder_name, 'policy_learning_history.csv'))
                pd.DataFrame(evaluation_dataframe).to_csv(os.path.join(
                    experiment_folder_name, EXPERIMENT_FULL_NAME))

    for eng_cf in interpretable_classifier:
        bc_classifier_unlimited_data = BehavioralCloning(classifier=eng_cf,
                                                         validation_split=0.2, cores_avail=cores_avail)
        for expert_algorithm in deep_learning_cloning_techniques:
            imitation_learning_names = ['VIPER Original']
            for imitation_learning_technique, imitation_learning_technique_name in zip([VIPERCloner],
                                                                                       imitation_learning_names):
                cloning_method_name = '%s -> %s Balanced ' % (expert_algorithm.abr_name, imitation_learning_technique_name)
                experiment_name = '%s -> %s %d leaf nodes' % (cloning_method_name, eng_cf.abr_name,
                                                              eng_cf.classifier.max_leaf_nodes)
                file_name = '_'.join(experiment_name.split(" "))
                experiment_folder_name = os.path.join(experiment_folder_template, file_name)
                if not os.path.exists(experiment_folder_name):
                    os.makedirs(experiment_folder_name)
                elif experiment_has_finished(experiment_folder_name):
                    print("We've already done %s " % experiment_name)
                    evaluation_dataframe = pd.read_csv(os.path.join(
                        experiment_folder_name, EXPERIMENT_FULL_NAME), index_col=0).to_dict(orient='list')
                    continue
                start_time = time()
                imitation_learning_technique = imitation_learning_technique(
                    imitation_learning_technique_name,
                    max_quality_change=2,
                    deterministic=True,
                    training_epochs=IMITATION_EPOCHS,
                    abr_policy_learner=eng_cf,
                    value_function_learner=value_function_learner, cores_avail=cores_avail,
                    weight_samples_method = 'Viper')
                imitation_learning_technique.clone_from_expert(
                    expert_algorithm=expert_algorithm,
                    streaming_enviroment=streaming_enviroment,
                    trace_list=expert_traces_training,
                    video_csv_list=expert_videos_training,
                    show_progress=True, expert_trajectory=expert_trajectory_training
                )
                imitation_learning_technique_scoring, imitation_learning_technique_evaluation = imitation_learning_technique.score(
                    expert_evaluation=expert_evaluation_validation,
                    expert_trajectory=expert_trajectory_validation,
                    streaming_enviroment=streaming_enviroment,
                    trace_list=expert_traces_validation,
                    video_csv_list=expert_videos_validation, add_data=True,
                )
                imitation_learning_technique_scoring['Provider'] = [provider]
                imitation_learning_technique_scoring['Base Classifier'] = [eng_cf.abr_name]
                imitation_learning_technique_scoring['Cloning Method'] = [cloning_method_name]
                imitation_learning_technique_scoring['Training Samples'] = [n_training_samples_full]
                imitation_learning_technique_scoring['Leaf Complexity'] = [eng_cf.classifier.max_leaf_nodes]
                imitation_learning_technique_scoring['Training Time'] = [time() - start_time]

                for k, v in imitation_learning_technique_scoring.items():
                    if k in evaluation_dataframe:
                        evaluation_dataframe[k] += v
                    else:
                        evaluation_dataframe[k] = v

                print('%s took %.2f s' % (experiment_name, time() - start_time))
                with open(os.path.join(experiment_folder_name, 'classifier'), 'wb') as output_file:
                    dill.dump(eng_cf, output_file)
                with open(os.path.join(experiment_folder_name, 'evaluation'), 'wb') as output_file:
                    dill.dump(imitation_learning_technique_evaluation, output_file)
                pd.DataFrame(imitation_learning_technique.policy_history).to_csv(
                    os.path.join(experiment_folder_name, 'policy_learning_history.csv'))
                pd.DataFrame(evaluation_dataframe).to_csv(os.path.join(
                    experiment_folder_name, EXPERIMENT_FULL_NAME))

        ############################################################################################################
        ### Try Copying via BC for different Settings
    print('Directly copying via BC %d' % len(direct_copy_algorithms))
    for classifier_comparison in interpretable_classifier:
        if hasattr(classifier_comparison, 'classifier') and hasattr(classifier_comparison.classifier, 'max_leaf_nodes'):
            experiment_name = '%s BC Full DAgger Balanced  %d leaf nodes' % (
                classifier_comparison.abr_name, classifier_comparison.classifier.max_leaf_nodes)
        else:
            experiment_name = '%s BC Full DAgger Balanced' % classifier_comparison.abr_name
        file_name = '_'.join(experiment_name.split(" "))
        experiment_folder_name = os.path.join(experiment_folder_template, file_name)
        if not os.path.exists(experiment_folder_name):
            os.makedirs(experiment_folder_name)
        elif experiment_has_finished(
                experiment_folder_name):
            evaluation_dataframe = pd.read_csv(os.path.join(
                experiment_folder_name, EXPERIMENT_FULL_NAME), index_col=0).to_dict(orient='list')
            print("We've already done %s " % experiment_name)
            continue
        start_time = time()
        bc_classifier_comparison = BehavioralCloningDAgger(classifier=classifier_comparison, validation_split=0.2,
                                                     cores_avail=cores_avail)
        bc_classifier_comparison.clone_from_trajectory(
            expert_trajectory=expert_trajectory_training,
            expert_evaluation=expert_evaluation_training,
            streaming_enviroment=streaming_enviroment,
            trace_list=expert_traces_training,
            video_csv_list=expert_videos_training,
        )
        bc_classifier_comparison_scoring, bc_classifier_comparison_evaluation = bc_classifier_comparison.score(
            expert_evaluation=expert_evaluation_validation,
            expert_trajectory=expert_trajectory_validation,
            streaming_enviroment=streaming_enviroment,
            trace_list=expert_traces_validation,
            video_csv_list=expert_videos_validation, add_data=True,
        )
        bc_classifier_comparison_scoring['Provider'] = [provider]
        bc_classifier_comparison_scoring['Base Classifier'] = [classifier_comparison.abr_name]
        bc_classifier_comparison_scoring['Cloning Method'] = ['BC Full DAgger Balanced']
        bc_classifier_comparison_scoring['Training Samples'] = [n_training_samples_full]
        leaf_complexity = 0
        if hasattr(classifier_comparison, 'classifier') and hasattr(classifier_comparison.classifier, 'max_leaf_nodes'):
            leaf_complexity = classifier_comparison.classifier.max_leaf_nodes
        bc_classifier_comparison_scoring['Leaf Complexity'] = [leaf_complexity]
        bc_classifier_comparison_scoring['Training Time'] = [time() - start_time]

        for k, v in bc_classifier_comparison_scoring.items():
            if k in evaluation_dataframe:
                evaluation_dataframe[k] += v
            else:
                evaluation_dataframe[k] = v
        print('%s took %.2f s' % (experiment_name, time() - start_time))
        with open(os.path.join(experiment_folder_name, 'classifier'), 'wb') as output_file:
            dill.dump(classifier_comparison, output_file)
        with open(os.path.join(experiment_folder_name, 'evaluation'), 'wb') as output_file:
            dill.dump(bc_classifier_comparison_evaluation, output_file)
        pd.DataFrame(bc_classifier_comparison.policy_history).to_csv(
            os.path.join(experiment_folder_name, 'policy_learning_history.csv'))
        pd.DataFrame(evaluation_dataframe).to_csv(os.path.join(
            experiment_folder_name, EXPERIMENT_FULL_NAME))

    interpretable_classifier = []
    interpretable_classifier += [ABRPolicyClassifierHandFeatureEngineering(
        abr_name='Complex Manual Feature Engineering', max_quality_change=2,
        deterministic=True,
        max_lookahead=3,
        max_lookback=10,
        classifier=DecisionTreeClassifier(max_leaf_nodes=MAX_INTERPRETABILITY, class_weight='balanced'),
        feature_complexity='complex')]

    ############################################################################################################
    ### Try Copying via BC for different Settings
    print('Directly copying via BC %d' % len(direct_copy_algorithms))
    for classifier_comparison in interpretable_classifier:
        if hasattr(classifier_comparison, 'classifier') and hasattr(classifier_comparison.classifier, 'max_leaf_nodes'):
            experiment_name = '%s BC Full Balanced  %d leaf nodes' % (
                classifier_comparison.abr_name, classifier_comparison.classifier.max_leaf_nodes)
        else:
            experiment_name = '%s BC Full Balanced' % classifier_comparison.abr_name
        file_name = '_'.join(experiment_name.split(" "))
        experiment_folder_name = os.path.join(experiment_folder_template, file_name)
        if not os.path.exists(experiment_folder_name):
            os.makedirs(experiment_folder_name)
        elif experiment_has_finished(
                experiment_folder_name):
            evaluation_dataframe = pd.read_csv(os.path.join(
                experiment_folder_name, EXPERIMENT_FULL_NAME), index_col=0).to_dict(orient='list')
            print("We've already done %s " % experiment_name)
            continue
        start_time = time()
        bc_classifier_comparison = BehavioralCloning(classifier=classifier_comparison, validation_split=0.2,
                                                     cores_avail=cores_avail)
        bc_classifier_comparison.clone_from_trajectory(
            expert_trajectory=expert_trajectory_training,
            expert_evaluation=expert_evaluation_training,
            streaming_enviroment=streaming_enviroment,
            trace_list=expert_traces_training,
            video_csv_list=expert_videos_training,
        )
        bc_classifier_comparison_scoring, bc_classifier_comparison_evaluation = bc_classifier_comparison.score(
            expert_evaluation=expert_evaluation_validation,
            expert_trajectory=expert_trajectory_validation,
            streaming_enviroment=streaming_enviroment,
            trace_list=expert_traces_validation,
            video_csv_list=expert_videos_validation, add_data=True,
        )
        bc_classifier_comparison_scoring['Provider'] = [provider]
        bc_classifier_comparison_scoring['Base Classifier'] = [classifier_comparison.abr_name]
        bc_classifier_comparison_scoring['Cloning Method'] = ['BC Full Balanced']
        bc_classifier_comparison_scoring['Training Samples'] = [n_training_samples_full]
        leaf_complexity = 0
        if hasattr(classifier_comparison, 'classifier') and hasattr(classifier_comparison.classifier, 'max_leaf_nodes'):
            leaf_complexity = classifier_comparison.classifier.max_leaf_nodes
        bc_classifier_comparison_scoring['Leaf Complexity'] = [leaf_complexity]
        bc_classifier_comparison_scoring['Training Time'] = [time() - start_time]

        for k, v in bc_classifier_comparison_scoring.items():
            if k in evaluation_dataframe:
                evaluation_dataframe[k] += v
            else:
                evaluation_dataframe[k] = v
        print('%s took %.2f s' % (experiment_name, time() - start_time))
        with open(os.path.join(experiment_folder_name, 'classifier'), 'wb') as output_file:
            dill.dump(classifier_comparison, output_file)
        with open(os.path.join(experiment_folder_name, 'evaluation'), 'wb') as output_file:
            dill.dump(bc_classifier_comparison_evaluation, output_file)
        pd.DataFrame(bc_classifier_comparison.policy_history).to_csv(
            os.path.join(experiment_folder_name, 'policy_learning_history.csv'))
        pd.DataFrame(evaluation_dataframe).to_csv(os.path.join(
            experiment_folder_name, EXPERIMENT_FULL_NAME))

    print('DAGGER/VIPER Copying Pipeline for engineered classifier')

    for eng_cf in interpretable_classifier:
        bc_classifier_unlimited_data = BehavioralCloning(classifier=eng_cf,
                                                         validation_split=0.2, cores_avail=cores_avail)
        for expert_algorithm in deep_learning_cloning_techniques:
            imitation_learning_names = ['DAGGER', 'VIPER']
            for imitation_learning_technique, imitation_learning_technique_name in zip([DAGGERCloner, VIPERCloner],
                                                                                       imitation_learning_names):
                cloning_method_name = '%s -> %s Balanced ' % (
                expert_algorithm.abr_name, imitation_learning_technique_name)
                experiment_name = '%s -> %s %d leaf nodes' % (cloning_method_name, eng_cf.abr_name,
                                                              eng_cf.classifier.max_leaf_nodes)
                file_name = '_'.join(experiment_name.split(" "))
                experiment_folder_name = os.path.join(experiment_folder_template, file_name)
                if not os.path.exists(experiment_folder_name):
                    os.makedirs(experiment_folder_name)
                elif experiment_has_finished(experiment_folder_name):
                    print("We've already done %s " % experiment_name)
                    evaluation_dataframe = pd.read_csv(os.path.join(
                        experiment_folder_name, EXPERIMENT_FULL_NAME), index_col=0).to_dict(orient='list')
                    continue
                start_time = time()
                imitation_learning_technique = imitation_learning_technique(
                    imitation_learning_technique_name,
                    max_quality_change=2,
                    deterministic=True,
                    training_epochs=IMITATION_EPOCHS,
                    abr_policy_learner=eng_cf,
                    value_function_learner=value_function_learner, cores_avail=cores_avail)
                imitation_learning_technique.clone_from_expert(
                    expert_algorithm=expert_algorithm,
                    streaming_enviroment=streaming_enviroment,
                    trace_list=expert_traces_training,
                    video_csv_list=expert_videos_training,
                    show_progress=True, expert_trajectory=expert_trajectory_training
                )
                imitation_learning_technique_scoring, imitation_learning_technique_evaluation = imitation_learning_technique.score(
                    expert_evaluation=expert_evaluation_validation,
                    expert_trajectory=expert_trajectory_validation,
                    streaming_enviroment=streaming_enviroment,
                    trace_list=expert_traces_validation,
                    video_csv_list=expert_videos_validation, add_data=True,
                )
                imitation_learning_technique_scoring['Provider'] = [provider]
                imitation_learning_technique_scoring['Base Classifier'] = [eng_cf.abr_name]
                imitation_learning_technique_scoring['Cloning Method'] = [cloning_method_name]
                imitation_learning_technique_scoring['Training Samples'] = [n_training_samples_full]
                imitation_learning_technique_scoring['Leaf Complexity'] = [eng_cf.classifier.max_leaf_nodes]
                imitation_learning_technique_scoring['Training Time'] = [time() - start_time]

                for k, v in imitation_learning_technique_scoring.items():
                    if k in evaluation_dataframe:
                        evaluation_dataframe[k] += v
                    else:
                        evaluation_dataframe[k] = v

                print('%s took %.2f s' % (experiment_name, time() - start_time))
                with open(os.path.join(experiment_folder_name, 'classifier'), 'wb') as output_file:
                    dill.dump(eng_cf, output_file)
                with open(os.path.join(experiment_folder_name, 'evaluation'), 'wb') as output_file:
                    dill.dump(imitation_learning_technique_evaluation, output_file)
                pd.DataFrame(imitation_learning_technique.policy_history).to_csv(
                    os.path.join(experiment_folder_name, 'policy_learning_history.csv'))
                pd.DataFrame(evaluation_dataframe).to_csv(os.path.join(
                    experiment_folder_name, EXPERIMENT_FULL_NAME))

    for eng_cf in interpretable_classifier:
        bc_classifier_unlimited_data = BehavioralCloning(classifier=eng_cf,
                                                         validation_split=0.2, cores_avail=cores_avail)
        for expert_algorithm in deep_learning_cloning_techniques:
            imitation_learning_names = ['VIPER Original']
            for imitation_learning_technique, imitation_learning_technique_name in zip([VIPERCloner],
                                                                                       imitation_learning_names):
                cloning_method_name = '%s -> %s Balanced ' % (
                expert_algorithm.abr_name, imitation_learning_technique_name)
                experiment_name = '%s -> %s %d leaf nodes' % (cloning_method_name, eng_cf.abr_name,
                                                              eng_cf.classifier.max_leaf_nodes)
                file_name = '_'.join(experiment_name.split(" "))
                experiment_folder_name = os.path.join(experiment_folder_template, file_name)
                if not os.path.exists(experiment_folder_name):
                    os.makedirs(experiment_folder_name)
                elif experiment_has_finished(experiment_folder_name):
                    print("We've already done %s " % experiment_name)
                    evaluation_dataframe = pd.read_csv(os.path.join(
                        experiment_folder_name, EXPERIMENT_FULL_NAME), index_col=0).to_dict(orient='list')
                    continue
                start_time = time()
                imitation_learning_technique = imitation_learning_technique(
                    imitation_learning_technique_name,
                    max_quality_change=2,
                    deterministic=True,
                    training_epochs=IMITATION_EPOCHS,
                    abr_policy_learner=eng_cf,
                    value_function_learner=value_function_learner, cores_avail=cores_avail,
                    weight_samples_method='Viper')
                imitation_learning_technique.clone_from_expert(
                    expert_algorithm=expert_algorithm,
                    streaming_enviroment=streaming_enviroment,
                    trace_list=expert_traces_training,
                    video_csv_list=expert_videos_training,
                    show_progress=True, expert_trajectory=expert_trajectory_training
                )
                imitation_learning_technique_scoring, imitation_learning_technique_evaluation = imitation_learning_technique.score(
                    expert_evaluation=expert_evaluation_validation,
                    expert_trajectory=expert_trajectory_validation,
                    streaming_enviroment=streaming_enviroment,
                    trace_list=expert_traces_validation,
                    video_csv_list=expert_videos_validation, add_data=True,
                )
                imitation_learning_technique_scoring['Provider'] = [provider]
                imitation_learning_technique_scoring['Base Classifier'] = [eng_cf.abr_name]
                imitation_learning_technique_scoring['Cloning Method'] = [cloning_method_name]
                imitation_learning_technique_scoring['Training Samples'] = [n_training_samples_full]
                imitation_learning_technique_scoring['Leaf Complexity'] = [eng_cf.classifier.max_leaf_nodes]
                imitation_learning_technique_scoring['Training Time'] = [time() - start_time]

                for k, v in imitation_learning_technique_scoring.items():
                    if k in evaluation_dataframe:
                        evaluation_dataframe[k] += v
                    else:
                        evaluation_dataframe[k] = v

                print('%s took %.2f s' % (experiment_name, time() - start_time))
                with open(os.path.join(experiment_folder_name, 'classifier'), 'wb') as output_file:
                    dill.dump(eng_cf, output_file)
                with open(os.path.join(experiment_folder_name, 'evaluation'), 'wb') as output_file:
                    dill.dump(imitation_learning_technique_evaluation, output_file)
                pd.DataFrame(imitation_learning_technique.policy_history).to_csv(
                    os.path.join(experiment_folder_name, 'policy_learning_history.csv'))
                pd.DataFrame(evaluation_dataframe).to_csv(os.path.join(
                    experiment_folder_name, EXPERIMENT_FULL_NAME))

    interpretable_classifier = []
    interpretable_classifier += [ABRPolicyClassifierHandFeatureEngineering(
        abr_name='Complex Manual Feature Engineering', max_quality_change=2,
        deterministic=True,
        max_lookahead=3,
        max_lookback=10,
        classifier=DecisionTreeClassifier(max_leaf_nodes=MAX_INTERPRETABILITY),
        feature_complexity='complex')]

    ############################################################################################################
    ### Try Copying via BC for different Settings
    print('Directly copying via BC %d' % len(direct_copy_algorithms))
    for classifier_comparison in interpretable_classifier:
        if hasattr(classifier_comparison, 'classifier') and hasattr(classifier_comparison.classifier, 'max_leaf_nodes'):
            experiment_name = '%s BC Full  %d leaf nodes' % (
                classifier_comparison.abr_name, classifier_comparison.classifier.max_leaf_nodes)
        else:
            experiment_name = '%s BC Full' % classifier_comparison.abr_name
        file_name = '_'.join(experiment_name.split(" "))
        experiment_folder_name = os.path.join(experiment_folder_template, file_name)
        if not os.path.exists(experiment_folder_name):
            os.makedirs(experiment_folder_name)
        elif experiment_has_finished(
                experiment_folder_name):
            evaluation_dataframe = pd.read_csv(os.path.join(
                experiment_folder_name, EXPERIMENT_FULL_NAME), index_col=0).to_dict(orient='list')
            print("We've already done %s " % experiment_name)
            continue
        start_time = time()
        bc_classifier_comparison = BehavioralCloning(classifier=classifier_comparison, validation_split=0.2,
                                                     cores_avail=cores_avail)
        bc_classifier_comparison.clone_from_trajectory(
            expert_trajectory=expert_trajectory_training,
            expert_evaluation=expert_evaluation_training,
            streaming_enviroment=streaming_enviroment,
            trace_list=expert_traces_training,
            video_csv_list=expert_videos_training,
        )
        bc_classifier_comparison_scoring, bc_classifier_comparison_evaluation = bc_classifier_comparison.score(
            expert_evaluation=expert_evaluation_validation,
            expert_trajectory=expert_trajectory_validation,
            streaming_enviroment=streaming_enviroment,
            trace_list=expert_traces_validation,
            video_csv_list=expert_videos_validation, add_data=True,
        )
        bc_classifier_comparison_scoring['Provider'] = [provider]
        bc_classifier_comparison_scoring['Base Classifier'] = [classifier_comparison.abr_name]
        bc_classifier_comparison_scoring['Cloning Method'] = ['BC Full']
        bc_classifier_comparison_scoring['Training Samples'] = [n_training_samples_full]
        leaf_complexity = 0
        if hasattr(classifier_comparison, 'classifier') and hasattr(classifier_comparison.classifier, 'max_leaf_nodes'):
            leaf_complexity = classifier_comparison.classifier.max_leaf_nodes
        bc_classifier_comparison_scoring['Leaf Complexity'] = [leaf_complexity]
        bc_classifier_comparison_scoring['Training Time'] = [time() - start_time]

        for k, v in bc_classifier_comparison_scoring.items():
            if k in evaluation_dataframe:
                evaluation_dataframe[k] += v
            else:
                evaluation_dataframe[k] = v
        print('%s took %.2f s' % (experiment_name, time() - start_time))
        with open(os.path.join(experiment_folder_name, 'classifier'), 'wb') as output_file:
            dill.dump(classifier_comparison, output_file)
        with open(os.path.join(experiment_folder_name, 'evaluation'), 'wb') as output_file:
            dill.dump(bc_classifier_comparison_evaluation, output_file)
        pd.DataFrame(bc_classifier_comparison.policy_history).to_csv(
            os.path.join(experiment_folder_name, 'policy_learning_history.csv'))
        pd.DataFrame(evaluation_dataframe).to_csv(os.path.join(
            experiment_folder_name, EXPERIMENT_FULL_NAME))

    print('DAGGER/VIPER Copying Pipeline for engineered classifier')

    for eng_cf in interpretable_classifier:
        bc_classifier_unlimited_data = BehavioralCloning(classifier=eng_cf,
                                                         validation_split=0.2, cores_avail=cores_avail)
        for expert_algorithm in deep_learning_cloning_techniques:
            imitation_learning_names = ['DAGGER', 'VIPER']
            for imitation_learning_technique, imitation_learning_technique_name in zip([DAGGERCloner, VIPERCloner],
                                                                                       imitation_learning_names):
                cloning_method_name = '%s -> %s' % (
                    expert_algorithm.abr_name, imitation_learning_technique_name)
                experiment_name = '%s -> %s %d leaf nodes' % (cloning_method_name, eng_cf.abr_name,
                                                              eng_cf.classifier.max_leaf_nodes)
                file_name = '_'.join(experiment_name.split(" "))
                experiment_folder_name = os.path.join(experiment_folder_template, file_name)
                if not os.path.exists(experiment_folder_name):
                    os.makedirs(experiment_folder_name)
                elif experiment_has_finished(experiment_folder_name):
                    print("We've already done %s " % experiment_name)
                    evaluation_dataframe = pd.read_csv(os.path.join(
                        experiment_folder_name, EXPERIMENT_FULL_NAME), index_col=0).to_dict(orient='list')
                    continue
                start_time = time()
                imitation_learning_technique = imitation_learning_technique(
                    imitation_learning_technique_name,
                    max_quality_change=2,
                    deterministic=True,
                    training_epochs=IMITATION_EPOCHS,
                    abr_policy_learner=eng_cf,
                    value_function_learner=value_function_learner, cores_avail=cores_avail,weight_samples_method='Divergence')
                imitation_learning_technique.clone_from_expert(
                    expert_algorithm=expert_algorithm,
                    streaming_enviroment=streaming_enviroment,
                    trace_list=expert_traces_training,
                    video_csv_list=expert_videos_training,
                    show_progress=True, expert_trajectory=expert_trajectory_training
                )
                imitation_learning_technique_scoring, imitation_learning_technique_evaluation = imitation_learning_technique.score(
                    expert_evaluation=expert_evaluation_validation,
                    expert_trajectory=expert_trajectory_validation,
                    streaming_enviroment=streaming_enviroment,
                    trace_list=expert_traces_validation,
                    video_csv_list=expert_videos_validation, add_data=True,
                )
                imitation_learning_technique_scoring['Provider'] = [provider]
                imitation_learning_technique_scoring['Base Classifier'] = [eng_cf.abr_name]
                imitation_learning_technique_scoring['Cloning Method'] = [cloning_method_name]
                imitation_learning_technique_scoring['Training Samples'] = [n_training_samples_full]
                imitation_learning_technique_scoring['Leaf Complexity'] = [eng_cf.classifier.max_leaf_nodes]
                imitation_learning_technique_scoring['Training Time'] = [time() - start_time]

                for k, v in imitation_learning_technique_scoring.items():
                    if k in evaluation_dataframe:
                        evaluation_dataframe[k] += v
                    else:
                        evaluation_dataframe[k] = v

                print('%s took %.2f s' % (experiment_name, time() - start_time))
                with open(os.path.join(experiment_folder_name, 'classifier'), 'wb') as output_file:
                    dill.dump(eng_cf, output_file)
                with open(os.path.join(experiment_folder_name, 'evaluation'), 'wb') as output_file:
                    dill.dump(imitation_learning_technique_evaluation, output_file)
                pd.DataFrame(imitation_learning_technique.policy_history).to_csv(
                    os.path.join(experiment_folder_name, 'policy_learning_history.csv'))
                pd.DataFrame(evaluation_dataframe).to_csv(os.path.join(
                    experiment_folder_name, EXPERIMENT_FULL_NAME))

    for eng_cf in interpretable_classifier:
        bc_classifier_unlimited_data = BehavioralCloning(classifier=eng_cf,
                                                         validation_split=0.2, cores_avail=cores_avail)
        for expert_algorithm in deep_learning_cloning_techniques:
            imitation_learning_names = ['VIPER Original']
            for imitation_learning_technique, imitation_learning_technique_name in zip([VIPERCloner],
                                                                                       imitation_learning_names):
                cloning_method_name = '%s -> %s' % (
                    expert_algorithm.abr_name, imitation_learning_technique_name)
                experiment_name = '%s -> %s %d leaf nodes' % (cloning_method_name, eng_cf.abr_name,
                                                              eng_cf.classifier.max_leaf_nodes)
                file_name = '_'.join(experiment_name.split(" "))
                experiment_folder_name = os.path.join(experiment_folder_template, file_name)
                if not os.path.exists(experiment_folder_name):
                    os.makedirs(experiment_folder_name)
                elif experiment_has_finished(experiment_folder_name):
                    print("We've already done %s " % experiment_name)
                    evaluation_dataframe = pd.read_csv(os.path.join(
                        experiment_folder_name, EXPERIMENT_FULL_NAME), index_col=0).to_dict(orient='list')
                    continue
                start_time = time()
                imitation_learning_technique = imitation_learning_technique(
                    imitation_learning_technique_name,
                    max_quality_change=2,
                    deterministic=True,
                    training_epochs=IMITATION_EPOCHS,
                    abr_policy_learner=eng_cf,
                    value_function_learner=value_function_learner, cores_avail=cores_avail,
                    weight_samples_method='Viper')
                imitation_learning_technique.clone_from_expert(
                    expert_algorithm=expert_algorithm,
                    streaming_enviroment=streaming_enviroment,
                    trace_list=expert_traces_training,
                    video_csv_list=expert_videos_training,
                    show_progress=True, expert_trajectory=expert_trajectory_training
                )
                imitation_learning_technique_scoring, imitation_learning_technique_evaluation = imitation_learning_technique.score(
                    expert_evaluation=expert_evaluation_validation,
                    expert_trajectory=expert_trajectory_validation,
                    streaming_enviroment=streaming_enviroment,
                    trace_list=expert_traces_validation,
                    video_csv_list=expert_videos_validation, add_data=True,
                )
                imitation_learning_technique_scoring['Provider'] = [provider]
                imitation_learning_technique_scoring['Base Classifier'] = [eng_cf.abr_name]
                imitation_learning_technique_scoring['Cloning Method'] = [cloning_method_name]
                imitation_learning_technique_scoring['Training Samples'] = [n_training_samples_full]
                imitation_learning_technique_scoring['Leaf Complexity'] = [eng_cf.classifier.max_leaf_nodes]
                imitation_learning_technique_scoring['Training Time'] = [time() - start_time]

                for k, v in imitation_learning_technique_scoring.items():
                    if k in evaluation_dataframe:
                        evaluation_dataframe[k] += v
                    else:
                        evaluation_dataframe[k] = v

                print('%s took %.2f s' % (experiment_name, time() - start_time))
                with open(os.path.join(experiment_folder_name, 'classifier'), 'wb') as output_file:
                    dill.dump(eng_cf, output_file)
                with open(os.path.join(experiment_folder_name, 'evaluation'), 'wb') as output_file:
                    dill.dump(imitation_learning_technique_evaluation, output_file)
                pd.DataFrame(imitation_learning_technique.policy_history).to_csv(
                    os.path.join(experiment_folder_name, 'policy_learning_history.csv'))
                pd.DataFrame(evaluation_dataframe).to_csv(os.path.join(
                    experiment_folder_name, EXPERIMENT_FULL_NAME))