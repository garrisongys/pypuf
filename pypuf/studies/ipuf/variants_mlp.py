"""
This module describes a study that defines a set of experiments in order to examine the quality of Deep Learning based
modeling attacks on Interpose PUFs variants. Furthermore, some plots are defined to visualize the experiment's results.

Results are used in Wisiol et al., "Splitting the Interpose PUF: A Novel Modeling Attack Strategy", CHES 2020.

References:
[1]  F. Rosenblatt,         "The Perceptron: A Probabilistic Model for Information Storage and Organization in the
                            Brain.", Psychological Review, volume 65, pp. 386-408, 1958.
[2]  D. Kingma and J. Ba,   “Adam: A Method for Stochastic Optimization”, arXiv:1412.6980, 2014.
[3]  F., Pedregosa et al.,  "Scikit-learn: Machine learning in Python", Journal of Machine Learning Research, volume 12,
                            pp. 2825-2830, 2011.
                            https://scikit-learn.org
"""
from os import getpid
from typing import NamedTuple, Iterable, List
from uuid import UUID
from uuid import uuid4

from numpy import concatenate, prod, sqrt, average, isinf, Inf
from numpy.core._multiarray_umath import ndarray
from numpy.random.mtrand import RandomState
from pandas import DataFrame

from pypuf import tools
from pypuf.experiments.experiment.base import Experiment
from pypuf.learner.neural_networks.mlp_skl import MultiLayerPerceptronScikitLearn
from pypuf.simulation.arbiter_based.arbiter_puf import XORArbiterPUF
from pypuf.simulation.arbiter_based.ltfarray import LTFArray
from pypuf.simulation.base import Simulation
from pypuf.studies.base import Study
from pypuf.studies.ipuf.split import SplitAttackStudy


class Interpose3PUF(Simulation):
    """
    The Domino-iPUF.
    """

    def __init__(self, n: int, k_up: int, k_middle: int, k_down: int, seed: int, noisiness: float = 0) -> None:
        self.seed = seed
        self.prng = RandomState(seed)
        self.n = n
        self.k = k_up
        self.k_up, self.k_middle, self.k_down = k_up, k_middle, k_down
        self.chains = k_up + k_middle + k_down
        self.xors = k_up if k_up > 1 else 0 + k_middle if k_middle > 1 else 0 + k_down if k_down > 1 else 0
        self.interposings = 3
        self.noisiness = noisiness
        seeds = [self.prng.randint(0, 2 ** 32) for _ in range(6)]
        self.up = XORArbiterPUF(n=n, k=k_up, seed=seeds[0], noisiness=noisiness, noise_seed=seeds[1])
        self.middle = XORArbiterPUF(n=n + 1, k=k_up, seed=seeds[2], noisiness=noisiness, noise_seed=seeds[3])
        self.down = XORArbiterPUF(n=n + 1, k=k_up, seed=seeds[4], noisiness=noisiness, noise_seed=seeds[5])
        self.interpose_pos = n // 2

    def __repr__(self) -> str:
        return f'Interpose3PUF, n={self.n}, k_up={self.k_up}, k_middle={self.k_middle}, k_down={self.k_down}, ' \
               f'pos={self.interpose_pos}'

    def challenge_length(self) -> int:
        return self.up.challenge_length()

    def response_length(self) -> int:
        return self.down.response_length()

    def _interpose(self, challenges, bits):
        pos = self.interpose_pos
        return concatenate(
            (challenges[:, :pos], bits.reshape(-1, 1), challenges[:, pos:]),
            axis=1,
        )

    def eval(self, challenges: ndarray) -> ndarray:
        return self.down.eval(self._interpose(
            challenges=challenges,
            bits=self.middle.eval(self._interpose(
                challenges=challenges,
                bits=self.up.eval(challenges)
            ))
        ))


class InterposeBinaryTree(Simulation):
    """
    The Tree-iPUF.
    """

    def __init__(self, n: int, ks: List[int], seed: int, noisiness: float = 0) -> None:
        self.seed = seed
        self.prng = RandomState(seed)
        self.n = n
        self.ks = ks
        self.k = ks[0]
        self.depth = len(ks) - 1
        self.chains = sum([k * (2 ** i) for i, k in enumerate(ks)])
        self.xors = sum([k * 2 ** i if k > 1 else 0 for i, k in enumerate(ks)])
        self.interposings = 2 ** (self.depth + 1) - 2
        self.noisiness = noisiness
        self.layers = \
            [
                [
                    XORArbiterPUF(
                        n=n + 1 if i > 0 else n,
                        k=ks[i],
                        seed=self.prng.randint(0, 2 ** 32),
                        noisiness=noisiness,
                        noise_seed=self.prng.randint(0, 2 ** 32)
                    )
                    for _ in range(2 ** i)
                ]
                for i in range(self.depth + 1)
            ]
        self.interpose_pos = n // 2

    def __repr__(self) -> str:
        return f'InterposeBinaryTree, n={self.n}, k={self.k}, depth={self.depth}, pos={self.interpose_pos}'

    def challenge_length(self) -> int:
        return self.layers[0][0].challenge_length()

    def response_length(self) -> int:
        return 1

    def _interpose(self, challenges, bits):
        pos = self.interpose_pos
        return concatenate(
            (challenges[:, :pos], bits.reshape(-1, 1), challenges[:, pos:]),
            axis=1,
        )

    def eval(self, challenges: ndarray) -> ndarray:
        responses = [self.layers[0][0].eval(challenges=challenges)]
        for i in range(self.depth - 1):
            responses = [self.layers[i + 1][j].eval(
                challenges=self._interpose(challenges=challenges, bits=responses[int(j / 2)])
            ) for j in range(len(self.layers[i + 1]))]
        return prod(responses, axis=0)


class InterposeCascade(Simulation):
    """
    The Cascade-iPUF.
    """

    def __init__(self, n: int, ks: List[int], seed: int, noisiness: float = 0) -> None:
        self.seed = seed
        self.prng = RandomState(seed)
        self.n = n
        self.k = ks[0]
        self.ks = ks
        self.chains = sum(ks)
        self.xors = self.chains
        self.interposings = len(ks)
        self.noisiness = noisiness
        seeds = [self.prng.randint(0, 2 ** 32) for _ in range(2 * len(ks))]
        self.layers = [
            XORArbiterPUF(
                n=n + 1 if i > 0 else n,
                k=k,
                seed=seeds[2 * i],
                noisiness=noisiness,
                noise_seed=seeds[2 * i + 1],
            )
            for i, k in enumerate(ks)
        ]
        self.interpose_pos = n // 2

    def __repr__(self) -> str:
        return f'InterposeCascade, n={self.n}, ks={str(self.ks)}, pos={self.interpose_pos}'

    def challenge_length(self) -> int:
        return self.layers[0].challenge_length()

    def response_length(self) -> int:
        return 1

    def _interpose(self, challenges, bits):
        pos = self.interpose_pos
        return concatenate(
            (challenges[:, :pos], bits.reshape(-1, 1), challenges[:, pos:]),
            axis=1,
        )

    def eval(self, challenges: ndarray) -> ndarray:
        result = 1
        for i, layer in enumerate(self.layers):
            result = result * layer.eval(self._interpose(challenges=challenges, bits=result) if i > 0 else challenges)
        return result


class XORInterposePUF(Simulation):
    """
    The XOR-iPUF.
    """

    def __init__(self, n: int, k: int, seed: int, noisiness: float = 0) -> None:
        self.seed = seed
        self.prng = RandomState(seed)
        self.n = n
        self.k = k
        self.chains = 2 * k
        self.xors = k
        self.interposings = k
        self.noisiness = noisiness
        seeds = [self.prng.randint(0, 2 ** 32) for _ in range(4 * k)]
        self.layers_up = [
            XORArbiterPUF(n=n, k=1, seed=seeds[2 * i], noisiness=noisiness, noise_seed=seeds[2 * i + 1])
            for i in range(k)
        ]
        self.layers_down = [
            XORArbiterPUF(n=n + 1, k=1, seed=seeds[2 * (i + k)], noisiness=noisiness, noise_seed=seeds[2 * (i + k) + 1])
            for i in range(k)
        ]
        self.interpose_pos = n // 2

    def __repr__(self) -> str:
        return f'XORInterposePUF, n={self.n}, k={self.k}, pos={self.interpose_pos}'

    def challenge_length(self) -> int:
        return self.layers_up[0].challenge_length()

    def response_length(self) -> int:
        return 1

    def _interpose(self, challenges, bits):
        pos = self.interpose_pos
        return concatenate(
            (challenges[:, :pos], bits.reshape(-1, 1), challenges[:, pos:]),
            axis=1,
        )

    def eval(self, challenges: ndarray) -> ndarray:
        return prod(
            a=[self.layers_down[i].eval(self._interpose(
                challenges=challenges,
                bits=self.layers_up[i].eval(challenges)
            )) for i in range(self.k)],
            axis=0,
        )


class XORInterpose3PUF(Simulation):
    """
    The XOR-Domino-iPUF.
    """

    def __init__(self, n: int, k: int, seed: int, noisiness: float = 0) -> None:
        self.seed = seed
        self.prng = RandomState(seed)
        self.n = n
        self.k = k
        self.chains = 3 * k
        self.xors = k
        self.interposings = 2 * k
        self.noisiness = noisiness
        seeds = [self.prng.randint(0, 2 ** 32) for _ in range(6 * k)]
        self.layers_up = [
            XORArbiterPUF(n=n, k=1, seed=seeds[2 * i], noisiness=noisiness, noise_seed=seeds[2 * i + 1])
            for i in range(k)
        ]
        self.layers_middle = [
            XORArbiterPUF(n=n + 1, k=1, seed=seeds[2 * (i + k)], noisiness=noisiness, noise_seed=seeds[2 * (i + k) + 1])
            for i in range(k)
        ]
        self.layers_down = [
            XORArbiterPUF(n=n + 1, k=1, seed=seeds[2 * (i+2*k)], noisiness=noisiness, noise_seed=seeds[2 * (i+2*k) + 1])
            for i in range(k)
        ]
        self.interpose_pos = n // 2

    def __repr__(self) -> str:
        return f'XORInterpose3PUF, n={self.n}, k={self.k}, pos={self.interpose_pos}'

    def challenge_length(self) -> int:
        return self.layers_up[0].challenge_length()

    def response_length(self) -> int:
        return 1

    def _interpose(self, challenges, bits):
        pos = self.interpose_pos
        return concatenate(
            (challenges[:, :pos], bits.reshape(-1, 1), challenges[:, pos:]),
            axis=1,
        )

    def eval(self, challenges: ndarray) -> ndarray:
        return prod(
            a=[self.layers_down[i].eval(self._interpose(
                challenges=challenges,
                bits=self.layers_middle[i].eval(self._interpose(
                    challenges=challenges,
                    bits=self.layers_up[i].eval(challenges)
                ))
            )) for i in range(self.k)],
            axis=0,
        )


class Parameters(NamedTuple):
    """
    Defines a iPUF-Variant to be modeled with MLP.
    """
    simulation: Simulation
    seed_simulation: int
    noisiness: float
    seed: int
    N: int
    validation_frac: float
    preprocessing: str
    layers: Iterable[int]
    learning_rate: float
    tolerance: float
    patience: int
    iteration_limit: int
    batch_size: int


class Result(NamedTuple):
    """
    Result of an attack on an iPUF-variant using MLP.
    """
    name: str
    n: int
    first_k: int
    num_chains: int
    num_xors: int
    num_interposings: int
    experiment_id: UUID
    pid: int
    measured_time: float
    iterations: int
    accuracy: float
    accuracy_relative: float
    stability: float
    reliability: float
    loss_curve: Iterable[float]
    accuracy_curve: Iterable[float]
    max_memory: int


class ExperimentMLPScikitLearn(Experiment):
    """
    Model an iPUF-variant using MLP.
    """

    NAME = 'Multilayer Perceptron (scikit-learn)'
    COMPRESSION = True

    def __init__(self, progress_log_prefix, parameters):
        self.id = uuid4()
        progress_log_name = None if not progress_log_prefix else f'{progress_log_prefix}_{self.id}'
        super().__init__(progress_log_name=progress_log_name, parameters=parameters)
        self.simulation = parameters.simulation
        self.stability = 1.0
        self.reliability = 1.0
        self.training_set = None
        self.learner = None
        self.model = None

    def prepare(self):
        self.stability = 1.0 - tools.approx_dist(
            instance1=self.simulation,
            instance2=self.simulation,
            num=10 ** 4,
            random_instance=RandomState(seed=self.parameters.seed),
        )
        self.stability = max(self.stability, 1 - self.stability)
        self.reliability = (1 + sqrt(2 * self.stability - 1)) / 2    # estimation of non-noisy vs. noisy
        self.progress_logger.debug(f'Gathering training set with {self.parameters.N} examples')
        self.training_set = tools.TrainingSet(
            instance=self.simulation,
            N=self.parameters.N,
            random_instance=RandomState(seed=self.parameters.seed),
        )
        self.progress_logger.debug('Setting up learner')
        self.learner = MultiLayerPerceptronScikitLearn(
            n=self.parameters.simulation.n,
            k=self.parameters.simulation.k,
            training_set=self.training_set,
            validation_frac=self.parameters.validation_frac,
            transformation=LTFArray.transform_atf,
            preprocessing='short',
            layers=self.parameters.layers,
            learning_rate=self.parameters.learning_rate,
            penalty=0.0002,
            beta_1=0.9,
            beta_2=0.999,
            tolerance=self.parameters.tolerance,
            patience=self.parameters.patience,
            iteration_limit=self.parameters.iteration_limit,
            batch_size=self.parameters.batch_size,
            seed_model=self.parameters.seed,
            print_learning=False,
            logger=self.progress_logger.debug,
            goal=0.95 * self.reliability,
        )
        self.learner.prepare()

    def run(self):
        if self.stability < 0.65:
            self.progress_logger.debug(f'The stability of the target is too low: {self.stability}')
            return
        self.progress_logger.debug('Starting learner')
        self.model = self.learner.learn()

    def analyze(self):
        self.progress_logger.debug('Analyzing result')
        accuracy = -1 if not self.model else 1.0 - tools.approx_dist(
            instance1=self.simulation,
            instance2=self.model,
            num=10 ** 4,
            random_instance=RandomState(seed=self.parameters.seed),
        )
        return Result(
            name=self.NAME,
            n=self.parameters.simulation.n,
            first_k=self.parameters.simulation.k,
            num_chains=self.parameters.simulation.chains,
            num_xors=self.parameters.simulation.xors,
            num_interposings=self.parameters.simulation.interposings,
            experiment_id=self.id,
            pid=getpid(),
            measured_time=self.measured_time,
            iterations=-1 if not self.model else self.learner.nn.n_iter_,
            accuracy=accuracy,
            accuracy_relative=accuracy / self.reliability,
            stability=self.stability,
            reliability=self.reliability,
            loss_curve=[-1] if not self.model else [round(loss, 3) for loss in self.learner.nn.loss_curve_],
            accuracy_curve=[-1] if not self.model else [round(accuracy, 3) for accuracy in self.learner.accuracy_curve],
            max_memory=self.max_memory(),
        )


class InterposeMLPStudy(Study):
    """
    A study containing a number of iPUF-variants for various parameterizations, conducting MLP-based modeling attacks.
    """
    SHUFFLE = True

    ITERATION_LIMIT = 400
    PATIENCE = ITERATION_LIMIT
    MAX_NUM_VAL = 10000
    MIN_NUM_VAL = 200
    PRINT_LEARNING = False
    LENGTH = 64
    SEED = 42
    NOISINESS = 0.1

    SAMPLES_PER_POINT = 100

    BATCH_FRAC = [0.05]

    def experiments(self):
        definitions = [
            definition for i in range(self.SAMPLES_PER_POINT) for definition in
            [
                (Interpose3PUF(self.LENGTH, 2, 1, 1, (self.SEED + 1000 + i) % 2 ** 32, self.NOISINESS),
                 [400000], [[2 ** 4] * 3], [0.02]),
                (Interpose3PUF(self.LENGTH, 2, 2, 2, (self.SEED + 2000 + i) % 2 ** 32, self.NOISINESS),
                 [400000], [[2 ** 4] * 3], [0.02]),
                (Interpose3PUF(self.LENGTH, 3, 1, 1, (self.SEED + 3000 + i) % 2 ** 32, self.NOISINESS),
                 [2000000], [[2 ** 6] * 3], [0.01]),
                (Interpose3PUF(self.LENGTH, 3, 3, 3, (self.SEED + 4000 + i) % 2 ** 32, self.NOISINESS),
                 [2000000], [[2 ** 6] * 3], [0.01]),
                (Interpose3PUF(self.LENGTH, 4, 1, 1, (self.SEED + 5000 + i) % 2 ** 32, self.NOISINESS),
                 [20000000], [[2 ** 7] * 3], [0.0075]),
                (Interpose3PUF(self.LENGTH, 4, 4, 4, (self.SEED + 6000 + i) % 2 ** 32, self.NOISINESS),
                 [20000000], [[2 ** 7] * 3], [0.0075]),
                (Interpose3PUF(self.LENGTH, 5, 1, 1, (self.SEED + 7000 + i) % 2 ** 32, self.NOISINESS),
                 [50000000], [[2 ** 8] * 3], [0.0001, 0.005]),
                (Interpose3PUF(self.LENGTH, 5, 5, 5, (self.SEED + 8000 + i) % 2 ** 32, self.NOISINESS),
                 [50000000], [[2 ** 8] * 3], [0.0001, 0.005]),

                (InterposeBinaryTree(self.LENGTH, [1, 1, 1], (self.SEED + 20000 + i) % 2 ** 32, self.NOISINESS),
                 [500000], [[2 ** 7] * 3], [0.008]),
                (InterposeBinaryTree(self.LENGTH, [2, 2, 2], (self.SEED + 22000 + i) % 2 ** 32, self.NOISINESS),
                 [5000000], [[2 ** 9] * 3], [0.004]),
                (InterposeBinaryTree(self.LENGTH, [1, 1, 1, 1], (self.SEED + 22000 + i) % 2 ** 32, self.NOISINESS),
                 [5000000], [[2 ** 9] * 3], [0.004]),

                (InterposeCascade(self.LENGTH, [1] * 2, (self.SEED + 40000 + i) % 2 ** 32, self.NOISINESS),
                 [80000], [[2 ** 2] * 3], [0.01]),
                (InterposeCascade(self.LENGTH, [1] * 3, (self.SEED + 41000 + i) % 2 ** 32, self.NOISINESS),
                 [120000], [[2 ** 3] * 3], [0.01]),
                (InterposeCascade(self.LENGTH, [1] * 4, (self.SEED + 42000 + i) % 2 ** 32, self.NOISINESS),
                 [200000], [[2 ** 4] * 3], [0.01]),
                (InterposeCascade(self.LENGTH, [1] * 5, (self.SEED + 43000 + i) % 2 ** 32, self.NOISINESS),
                 [400000], [[2 ** 5] * 3], [0.01]),
                (InterposeCascade(self.LENGTH, [1] * 6, (self.SEED + 44000 + i) % 2 ** 32, self.NOISINESS),
                 [1000000], [[2 ** 6] * 3], [0.01]),
                (InterposeCascade(self.LENGTH, [1] * 7, (self.SEED + 45000 + i) % 2 ** 32, self.NOISINESS),
                 [30000000], [[2 ** 7] * 3], [0.01]),
                (InterposeCascade(self.LENGTH, [1] * 8, (self.SEED + 46000 + i) % 2 ** 32, self.NOISINESS),
                 [10000000], [[2 ** 8] * 3], [0.01]),
                (InterposeCascade(self.LENGTH, [2] * 2, (self.SEED + 47000 + i) % 2 ** 32, self.NOISINESS),
                 [200000], [[2 ** 4] * 3], [0.02]),
                (InterposeCascade(self.LENGTH, [2] * 3, (self.SEED + 48000 + i) % 2 ** 32, self.NOISINESS),
                 [500000], [[2 ** 5] * 3], [0.02]),
                (InterposeCascade(self.LENGTH, [2] * 4, (self.SEED + 49000 + i) % 2 ** 32, self.NOISINESS),
                 [2000000], [[2 ** 6] * 3], [0.02]),
                (InterposeCascade(self.LENGTH, [2] * 5, (self.SEED + 50000 + i) % 2 ** 32, self.NOISINESS),
                 [10000000], [[2 ** 7] * 3], [0.005]),
                (InterposeCascade(self.LENGTH, [3] * 2, (self.SEED + 51000 + i) % 2 ** 32, self.NOISINESS),
                 [2000000], [[2 ** 7] * 3], [0.003]),
                (InterposeCascade(self.LENGTH, [3] * 3, (self.SEED + 52000 + i) % 2 ** 32, self.NOISINESS),
                 [10000000], [[2 ** 7] * 3], [0.002]),
                (InterposeCascade(self.LENGTH, [4] * 2, (self.SEED + 53000 + i) % 2 ** 32, self.NOISINESS),
                 [5000000], [[2 ** 8] * 3], [0.001]),
                (InterposeCascade(self.LENGTH, [5] * 2, (self.SEED + 54000 + i) % 2 ** 32, self.NOISINESS),
                 [20000000], [[2 ** 8] * 3], [0.001]),

                (XORInterposePUF(self.LENGTH, 2, (self.SEED + 60000 + i) % 2 ** 32, self.NOISINESS),
                 [100000], [[2 ** 4] * 3], [0.01]),
                (XORInterposePUF(self.LENGTH, 3, (self.SEED + 61000 + i) % 2 ** 32, self.NOISINESS),
                 [400000], [[2 ** 5] * 3], [0.01]),
                (XORInterposePUF(self.LENGTH, 4, (self.SEED + 62000 + i) % 2 ** 32, self.NOISINESS),
                 [10000000], [[2 ** 7] * 3], [0.005]),
                (XORInterposePUF(self.LENGTH, 5, (self.SEED + 63000 + i) % 2 ** 32, self.NOISINESS),
                 [40000000], [[2 ** 8] * 3], [0.0025]),

                (XORInterpose3PUF(self.LENGTH, 2, (self.SEED + 80000 + i) % 2 ** 32, self.NOISINESS),
                 [200000], [[2 ** 4] * 3], [0.01]),
                (XORInterpose3PUF(self.LENGTH, 3, (self.SEED + 81000 + i) % 2 ** 32, self.NOISINESS),
                 [2000000], [[2 ** 5] * 3], [0.01]),
                (XORInterpose3PUF(self.LENGTH, 4, (self.SEED + 82000 + i) % 2 ** 32, self.NOISINESS),
                 [40000000], [[2 ** 8] * 3], [0.0025]),
            ]
        ]

        return [
            ExperimentMLPScikitLearn(
                progress_log_prefix=self.name(),
                parameters=Parameters(
                    simulation=simulation,
                    seed_simulation=simulation.seed,
                    noisiness=simulation.noisiness,
                    seed=self.SEED + i,
                    N=N,
                    validation_frac=max(min(N // 20, self.MAX_NUM_VAL), self.MIN_NUM_VAL) / N,
                    preprocessing='short',
                    layers=layers,
                    learning_rate=learning_rate,
                    tolerance=0.0025,
                    patience=self.PATIENCE,
                    iteration_limit=self.ITERATION_LIMIT,
                    batch_size=int(N * batch_frac),
                )
            )
            for i, (simulation, Ns, structures, learning_rates) in enumerate(definitions)
            for N in Ns
            for layers in structures
            for learning_rate in learning_rates
            for batch_frac in self.BATCH_FRAC
        ]

    def plot(self):
        data = self.experimenter.results

        data['success'] = data.apply(lambda row: row['accuracy_relative'] >= .90, axis=1)
        data['threads'] = data.apply(SplitAttackStudy.num_threads, axis=1)

        groups = data.groupby(['N', 'simulation', 'num_chains', 'threads', 'cpu'])
        rt_data = DataFrame(columns=['N', 'simulation', 'num_chains', 'threads', 'cpu',
                                     'success_rate', 'avg_time_success', 'avg_time_fail', 'num_success', 'num_fail',
                                     'num_total', 'time_to_success', 'reliability', 'memory_avg_gib', 'memory_max_gib',
                                     'avg_rel_accuracy'])
        for (N, simulation, num_chains, threads, cpu), g_data in groups:
            num_success = len(g_data[g_data['success']].index)
            num_total = len(g_data.index)
            success_rate = num_success / num_total
            mean_time_success = average(g_data[g_data['success']]['measured_time'])
            mean_time_fail = average(g_data[~g_data['success']]['measured_time']) if success_rate < 1 else 0
            exp_number_of_trials_until_success = 1 / success_rate if success_rate > 0 else Inf  # Geometric dist.
            if isinf(exp_number_of_trials_until_success):
                time_to_success = Inf
            else:
                time_to_success = (exp_number_of_trials_until_success - 1) * mean_time_fail + mean_time_success
            reliability = g_data['reliability'].mean()
            rt_data = rt_data.append(
                {
                    'N': N, 'simulation': simulation, 'num_chains': num_chains, 'threads': threads, 'cpu': cpu,
                    'success_rate': success_rate,
                    'avg_time_success': mean_time_success,
                    'avg_time_fail': mean_time_fail,
                    'num_success': num_success,
                    'num_fail': num_total - num_success,
                    'num_total': num_total,
                    'time_to_success': time_to_success,
                    'reliability': round(reliability * 100 // 10 * 10 / 100, 2),
                    'memory_avg_gib': g_data['max_memory'].mean() / 1024**3,
                    'memory_max_gib': g_data['max_memory'].max() / 1024**3,
                    'avg_rel_accuracy': g_data['accuracy_relative'].mean(),
                },
                ignore_index=True,
            )
        rt_data = rt_data.sort_values(['simulation', 'num_chains', 'N', 'reliability'])
        rt_data['time_to_success'] = rt_data.apply(lambda row: SplitAttackStudy.time_cat(row['time_to_success']),
                                                   axis=1)

        table_cols = ['simulation_friendly_name', 'simulation', 'num_chains', 'N_cat', 'reliability', 'memory_avg_gib',
                      'time_to_success', 'success_rate', 'num_total']

        def reader_friendly_name(simulation_name):
            for technical_name, friendly_name in {
                    'Interpose3PUF': 'Domino-iPUF',
                    'XORInterposePUF': 'XOR-iPUF',
                    'XORInterpose3PUF': 'XOR-Domino-iPUF',
                    'InterposeBinaryTree': 'Tree-iPUF',
                    'InterposeCascade': 'Cascade-iPUF',
            }.items():
                if str(simulation_name).startswith(technical_name):
                    return friendly_name
            return simulation_name

        rt_data['simulation_friendly_name'] = rt_data.apply(lambda row: reader_friendly_name(row['simulation']), axis=1)
        rt_data['success_rate'] = rt_data['success_rate'].round(2)
        rt_data['memory_avg_gib'] = rt_data['memory_avg_gib'].round(1)
        rt_data['num_chains'] = rt_data['num_chains'].astype('int')
        rt_data['N_cat'] = rt_data.apply(lambda row: SplitAttackStudy.N_cat(row['N']), axis=1)

        print(rt_data[rt_data['num_chains'] >= 8][table_cols].to_latex(index=False, escape=False))
