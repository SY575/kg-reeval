import tensorflow as tf
import numpy as np
from scipy.stats import rankdata
tf.get_logger().setLevel('INFO')
tf.autograph.set_verbosity(1)

from collections import defaultdict as ddict

np.random.seed(1234)
import os
import time
import datetime, pickle
from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter
from builddata import *
from model import ConvKB
from functools import reduce
from operator import mul


# Parameters
# ==================================================
parser = ArgumentParser("CapsE", formatter_class=ArgumentDefaultsHelpFormatter, conflict_handler='resolve')

parser.add_argument("--data", default="./data/", help="Data sources.")
parser.add_argument("--run_folder", default="../", help="Data sources.")
parser.add_argument("--name", default="WN18RR", help="Name of the dataset.")

parser.add_argument("--embedding_dim", default=50, type=int, help="Dimensionality of character embedding")
parser.add_argument("--filter_sizes", default="1", help="Comma-separated filter sizes")
parser.add_argument("--num_filters", default=500, type=int, help="Number of filters per filter size")
parser.add_argument("--dropout_keep_prob", default=1.0, type=float, help="Dropout keep probability")
parser.add_argument("--l2_reg_lambda", default=0.001, type=float, help="L2 regularization lambda")
parser.add_argument("--learning_rate", default=0.0001, type=float, help="Learning rate")
parser.add_argument("--is_trainable", default=True, type=bool, help="")
parser.add_argument("--batch_size", default=128, type=int, help="Batch Size")
parser.add_argument("--neg_ratio", default=1.0, type=float, help="Number of negative triples generated by positive")
parser.add_argument("--use_pretrained", default=True, type=bool, help="Using the pretrained embeddings")
parser.add_argument("--num_epochs", default=201, type=int, help="Number of training epochs")
parser.add_argument("--saveStep", default=200, type=int, help="")
parser.add_argument("--allow_soft_placement", default=True, type=bool, help="Allow device soft device placement")
parser.add_argument("--log_device_placement", default=False, type=bool, help="Log placement of ops on devices")
parser.add_argument("--model_name", default='wn18rr', help="")
parser.add_argument("--useConstantInit", action='store_true')

parser.add_argument("--model_index", default='200', help="")
parser.add_argument("--num_splits", default=8, type=int, help="Split the validation set into 8 parts for a faster evaluation")
parser.add_argument("--testIdx", default=1, type=int, help="From 0 to 7. Index of one of 8 parts")
parser.add_argument("--decode", action='store_false')

parser.add_argument("--eval_type", default='random', help="Evaluation Protocol to use. Options: top/bottom/random")

args = parser.parse_args()
print(args)

# Load data
print("Loading data...")

train, valid, test, words_indexes, indexes_words, \
headTailSelector, entity2id, id2entity, relation2id, id2relation = build_data(path=args.data, name=args.name)
data_size = len(train)
train_batch = Batch_Loader(train, words_indexes, indexes_words, headTailSelector, entity2id, id2entity, relation2id, id2relation, batch_size=args.batch_size, neg_ratio=args.neg_ratio)

entity_array = np.array(list(train_batch.indexes_ents.keys()))

lstEmbed = []
if args.use_pretrained == True:
	print("Using pre-trained model.")
	lstEmbed = np.empty([len(words_indexes), args.embedding_dim]).astype(np.float32)
	initEnt, initRel = init_norm_Vector(args.data + args.name + '/relation2vec' + str(args.embedding_dim) + '.init', args.data + args.name + '/entity2vec' + str(args.embedding_dim) + '.init', args.embedding_dim)
	for _word in words_indexes:
		if _word in relation2id:
			index = relation2id[_word]
			_ind = words_indexes[_word]
			lstEmbed[_ind] = initRel[index]
		elif _word in entity2id:
			index = entity2id[_word]
			_ind = words_indexes[_word]
			lstEmbed[_ind] = initEnt[index]
		else:
			print('*****************Error********************!')
			break
	lstEmbed = np.array(lstEmbed, dtype=np.float32)

assert len(words_indexes) % (len(entity2id) + len(relation2id)) == 0

print("Loading data... finished!")

x_valid = np.array(list(valid.keys())).astype(np.int32)
y_valid = np.array(list(valid.values())).astype(np.float32)
len_valid = len(x_valid)
batch_valid = int(len_valid / (args.num_splits))

x_test = np.array(list(test.keys())).astype(np.int32)
y_test = np.array(list(test.values())).astype(np.float32)
len_test = len(x_test)
batch_test = int(len_test / (args.num_splits))

# uncomment when tuning hyper-parameters on the validation set
# x_test = x_valid
# y_test = y_valid
# len_test = len_valid
# batch_test = batch_valid

##########################################

if args.decode == False:
	lstModelNames = list(args.model_name.split(","))
	for _model_name in lstModelNames:
		out_dir = os.path.abspath(os.path.join(args.run_folder, "runs", _model_name))
		print("Evaluating {}\n".format(out_dir))
		checkpoint_dir = os.path.abspath(os.path.join(out_dir, "checkpoints"))
		checkpoint_prefix = os.path.join(checkpoint_dir, "model")

		lstModelIndexes = list(args.model_index.split(","))
		for _model_index in lstModelIndexes:
			_file = checkpoint_prefix + "-" + _model_index
			lstHT = []
			for _index in range(args.num_splits):
				with open(_file + '.eval_{}.'.format(args.eval_type) + str(_index) + '.txt') as f:
					for _line in f:
						if _line.strip() != '':
							lstHT.append(list(map(float, _line.strip().split())))
			lstHT = np.array(lstHT)
			results = np.round(np.sum(lstHT, axis=0) / (2 * len_test), 3)
			print(_file, 'mr, mrr, hits@10 --> ', results)
			print('{},{:.3},{:.3},{:.3},{:.3}'.format(int(results[0]), results[1], results[4], results[3],results[2]))

		print('------------------------------------')

else:
	with tf.Graph().as_default():
		tf.set_random_seed(1234)
		session_conf = tf.ConfigProto(allow_soft_placement=args.allow_soft_placement,
									  log_device_placement=args.log_device_placement)
		session_conf.gpu_options.allow_growth = True
		sess = tf.Session(config=session_conf)
		all_pool_zeros = []
		all_pool_tot = []

		with sess.as_default():
			global_step = tf.Variable(0, name="global_step", trainable=False)

			cnn = ConvKB(
				sequence_length=x_valid.shape[1],  # 3
				num_classes=y_valid.shape[1],  # 1
				pre_trained=lstEmbed,
				embedding_size=args.embedding_dim,
				filter_sizes=list(map(int, args.filter_sizes.split(","))),
				num_filters=args.num_filters,
				vocab_size=len(words_indexes),
				l2_reg_lambda=args.l2_reg_lambda,
				is_trainable=args.is_trainable,
				useConstantInit=args.useConstantInit)

			# Output directory for models and summaries

			lstModelNames = list(args.model_name.split(","))

			for _model_name in lstModelNames:

				out_dir = os.path.abspath(os.path.join(args.run_folder, "runs", _model_name))
				print("Evaluating {}\n".format(out_dir))

				# Checkpoint directory. Tensorflow assumes this directory already exists so we need to create it
				checkpoint_dir = os.path.abspath(os.path.join(out_dir, "checkpoints"))
				checkpoint_prefix = os.path.join(checkpoint_dir, "model")

				lstModelIndexes = list(args.model_index.split(","))

				for _model_index in lstModelIndexes:

					_file = checkpoint_prefix + "-" + _model_index

					cnn.saver.restore(sess, _file)

					print("Loaded model", _file)


					# Predict function to predict scores for test data
					def predict(x_batch, y_batch, writer=None, pdb_use=False):
						feed_dict = {
							cnn.input_x: x_batch,
							cnn.input_y: y_batch,
							cnn.dropout_keep_prob: 1.0,
						}
						scores, h_pool, rest = sess.run([cnn.predictions, cnn.h_pool, (cnn.W, cnn.b, cnn.scores, cnn.h_nonpool, cnn.pool_b)], feed_dict)

						all_pool_zeros.append((h_pool == 0).sum(-1).sum(-1))
						all_pool_tot.append([h_pool.shape[0], h_pool.shape[2] * h_pool.shape[3]])

						return scores


					res = []

					def test_prediction(x_batch, y_batch, head_or_tail='head'):

						hits10 = 0.0
						hits1 = 0.0
						hits3 = 0.0
						mrr = 0.0
						mr = 0.0

						eval_type = args.eval_type
						global entity_array


						for i in range(len(x_batch)):
							new_x_batch = np.tile(x_batch[i], (len(entity2id), 1))
							new_y_batch = np.tile(y_batch[i], (len(entity2id), 1))

							if head_or_tail == 'head': new_x_batch[:, 0] = entity_array
							else: 			   new_x_batch[:, 2] = entity_array

							lstIdx = []
							for tmpIdxTriple in range(len(new_x_batch)):
								tmpTriple = (new_x_batch[tmpIdxTriple][0], new_x_batch[tmpIdxTriple][1],
											 new_x_batch[tmpIdxTriple][2])
								if (tmpTriple in train) or (tmpTriple in valid) or (tmpTriple in test): #also remove the valid test triple
									lstIdx.append(tmpIdxTriple)
							new_x_batch = np.delete(new_x_batch, lstIdx, axis=0)
							new_y_batch = np.delete(new_y_batch, lstIdx, axis=0)

							if eval_type == 'top':
								new_x_batch = np.insert(new_x_batch, 0, x_batch[i], axis=0)
								new_y_batch = np.insert(new_y_batch, 0, y_batch[i], axis=0)

							elif eval_type == 'bottom':
								new_x_batch = np.concatenate([new_x_batch, [x_batch[i]]], 0)
								new_y_batch = np.concatenate([new_y_batch, [y_batch[i]]], 0)

							elif eval_type == 'random':
								rand   = np.random.randint(new_x_batch.shape[0])
								new_x_batch = np.insert(new_x_batch, rand, x_batch[i], axis=0)
								new_y_batch = np.insert(new_y_batch, rand, y_batch[i], axis=0)

							else:
								raise NotImplementedError

							results = []
							listIndexes = range(0, len(new_x_batch), (int(args.neg_ratio) + 1) * args.batch_size)

							for tmpIndex in range(len(listIndexes) - 1):
								results = np.append(results, predict(
									new_x_batch[listIndexes[tmpIndex]: listIndexes[tmpIndex + 1]],
									new_y_batch[listIndexes[tmpIndex]: listIndexes[tmpIndex + 1]], pdb_use = i == 8))

							results = np.append(results, predict(new_x_batch[listIndexes[-1]:], new_y_batch[listIndexes[-1]:]))
							results = np.reshape(results, -1)

							if eval_type == 'top':
								sorted_indices = np.argsort(results, axis=-1, kind='stable')
								_filter = np.where(sorted_indices == 0)[0][0]
								res.append({
									'rand_pos': 0,
									'results': results
								})

							elif eval_type == 'bottom':
								sorted_indices = np.argsort(results, axis=-1, kind='stable')
								_filter = np.where(sorted_indices == sorted_indices.shape[0]-1)[0][0]
								res.append({
									'rand_pos': sorted_indices.shape[0]-1,
									'results': results
								})

							elif eval_type == 'random':
								sorted_indices = np.argsort(results, axis=-1, kind='stable')
								_filter = np.where(sorted_indices == rand)[0][0]
								res.append({
									'rand_pos': rand,
									'results': results
								})

							mr  += (_filter + 1)
							mrr += 1.0 / (_filter + 1)
							if _filter < 10: hits10 += 1
							if _filter < 1: hits1 += 1
							if _filter < 3: hits3 += 1

						return np.array([mr, mrr, hits1, hits3, hits10])

					if args.testIdx < (args.num_splits - 1):
						head_results = test_prediction(x_test[batch_test * args.testIdx: batch_test * (args.testIdx + 1)], y_test[batch_test * args.testIdx: batch_test * (args.testIdx + 1)], head_or_tail='head')
						tail_results = test_prediction(x_test[batch_test * args.testIdx: batch_test * (args.testIdx + 1)], y_test[batch_test * args.testIdx: batch_test * (args.testIdx + 1)], head_or_tail='tail')
					else:
						head_results = test_prediction(x_test[batch_test * args.testIdx: len_test], y_test[batch_test * args.testIdx: len_test], head_or_tail='head')
						tail_results = test_prediction(x_test[batch_test * args.testIdx: len_test], y_test[batch_test * args.testIdx: len_test], head_or_tail='tail')

					wri = open(_file + '.eval_{}.'.format(args.eval_type) + str(args.testIdx) + '.txt', 'w')

					for _val in head_results:
						wri.write(str(_val) + ' ')
					wri.write('\n')
					for _val in tail_results:
						wri.write(str(_val) + ' ')
					wri.write('\n')

					# print('{}/{}'.format(np.sum(all_pool_zeros), np.sum(all_pool_tot)))
					pickle.dump({'all_pool_zeros': all_pool_zeros, 'all_pool_tot': all_pool_tot}, open(_file + '.pool_{}.'.format(args.eval_type) + str(args.testIdx) + '.pkl', 'wb'))

					pickle.dump(res, open(_file + '.eval_{}.'.format(args.eval_type) + str(args.testIdx) + '.pkl', 'wb'))

					wri.close()
