import numpy as np
import itertools
from pypuf import tools
from pypuf.simulation.arbiter_based.ltfarray import LTFArray

class Reliability_based_CMA_ES():

    # recommended properties of parameters:
    #   pop_size=30
    #   parent_size=10 (>= 0.3*pop_size)
    #   priorities: linear low decreasing
    #   repeat = 5
    def __init__(self, instance, pop_size, parent_size, priorities,
                 challenge_num, repeat, unreliability, precision):
        self.n = instance.n + 1                                     # length of LTFs plus 1 because of epsilon
        self.different_LTFs = np.zeros((instance.k, self.n))        # learned LTFs
        self.unreliability = unreliability                          # proportion of unreliable challenges
        self.precision = precision                                  # precision of every LTF
        self.num_of_LTFs = 0                                        # number of different learned LTFs
        self.instance = instance                                    # simulation instance to be modelled
        self.challenge_num = challenge_num                          # number of challenges used
        self.repeat = repeat                                        # frequency of same repeated challenge
        self.individuals = np.zeros((pop_size, self.n))             # weight_arrays plus epsilon
        # mean, step_size,  pop_size,   parent_size,    priorities, cov_matrix,   path_cm,    path_ss
        # m,    sigma,      lambda,     mu,             w_i,        C,            p_c,        p_sigma
        self.mean = np.zeros(self.n)                                # mean vector of distribution
        self.step_size = 1                                          # distance to next distribution
        self.pop_size = pop_size                                    # number of individuals per generation
        self.parent_size = parent_size                              # number of considered individuals
        self.priorities = priorities                                # array of consideration proportion
        self.cov_matrix = np.identity(self.n)                       # shape of distribution
        self.path_cm = np.zeros(self.n)                             # cumulated evolution path of covariance matrix
        self.path_ss = np.zeros(self.n)                             # cumulated evolution path of step size
        # auxiliary constants
        self.mu_w = 1 / np.sum(np.square(priorities))
        self.c_mu = self.mu_w / self.n**2
        self.d_sigma = 1 + np.sqrt(self.mu_w / self.n)
        self.c_1 = 2 / self.n**2
        self.c_c = 4 / self.n
        self.c_sigma = 4 / self.n
        assert len(priorities) == parent_size
        assert int(np.ndarray.sum(priorities)) == 1
        assert (self.c_1 + self.c_mu <= 1)

    def learn(self):
        # this is the main learning method
        # returns XOR-LTFArray with nearly same behavior as learned instance
        while self.num_of_LTFs < self.instance.k:
            new_LTF = self.learn_LTF()
            if self.is_different_LTF(new_LTF):
                self.different_LTFs[self.num_of_LTFs] = new_LTF
                self.num_of_LTFs += 1
        self.different_LTFs = self.set_pole_of_LTFs(self.instance, self.different_LTFs)
        return LTFArray(self.different_LTFs, LTFArray.transform_atf, LTFArray.combiner_xor, bias=False)

    def is_different_LTF(self, new_LTF):
        # returns True iff new_LTF is different from previously learned LTFs
        if self.num_of_LTFs == 0:
            return True
        weight_arrays = self.different_LTFs[:self.num_of_LTFs, :]
        new_LTFArray = LTFArray(new_LTF[:, :-1], LTFArray.transform_atf, LTFArray.combiner_xor)
        different_LTFs = self.build_LTFArrays(weight_arrays[:, :-1])
        challenges = np.array(list(tools.sample_inputs(self.instance.n, self.challenge_num)))
        responses = np.empty((self.num_of_LTFs, self.challenge_num))
        responses[0, :] = new_LTFArray.eval(challenges)
        for i, current_LTF in enumerate(different_LTFs):
            responses[i+1, :] = current_LTF.eval(challenges)
        return not self.is_correlated(responses)

    # TODO
    def learn_LTF(self):
        # this is the main CMA-ES algorithm like that from Hansen
        terminate = False
        new_LTF = np.empty(np.shape(self.individuals)[1])
        x = 0
        while not terminate:
            x = x+1
            print('x =', x)
            self.individuals = self.reproduce(self.mean, self.cov_matrix, self.pop_size, self.step_size)
            #print('individuals:\n', self.individuals)
            challenges = np.array(list(tools.sample_inputs(self.instance.n, self.challenge_num)))
            measured_rels = self.measure_rels(self.instance, challenges, self.challenge_num, self.repeat)
            print('measured_rels:\n', measured_rels)
            correlations = self.fitness(challenges, self.challenge_num, measured_rels, self.individuals)
            print('correlations:\n', correlations)
            for i in range(np.shape(correlations)[0]):
                if correlations[i] > self.precision:
                    new_LTF = self.individuals[i, :]
                    terminate = True
            sorted_individuals = __class__.sort_individuals(self.individuals, correlations)
            if terminate:
                break
            parent = self.get_parent(sorted_individuals, self.parent_size, self.priorities)
            print('parent:\n', parent)
            cm_mu = self.get_cm_mu(sorted_individuals, self.parent_size, self.priorities)
            #print('cm_mu:\n', cm_mu)
            self.mean = self.update_mean(self.mean, self.step_size, parent)
            print('mean:\n', self.mean)
            self.path_cm = self.cumulation_for_cm(self.path_cm, self.c_c, self.path_ss, self.n,
                                                  self.mu_w, parent)
            print('path_cm:\n', self.path_cm, '\nlength:', np.linalg.norm(self.path_cm))
            self.path_ss = self.cumulation_for_ss(self.path_ss, self.c_sigma, self.mu_w, self.cov_matrix, parent)
            print('path_ss:\n', self.path_ss, '\nlength:', np.linalg.norm(self.path_ss))
            self.cov_matrix = self.update_cm(self.cov_matrix, self.c_1, self.c_mu, self.path_cm, cm_mu)
            print('cov_matrix:\n', self.cov_matrix)
            self.step_size = self.update_ss(self.step_size, self.c_sigma, self.d_sigma, self.path_ss)
            print('step_size:\n', self.step_size)
        return new_LTF


    # updating methods of evolution strategies
    @staticmethod
    def reproduce(mean, cov_matrix, pop_size, step_size):
        # returns a new generation of individuals as 2D array (pop_size, n)
        mutations = np.random.multivariate_normal(np.zeros(np.shape(mean)), cov_matrix, pop_size)
        duplicated_mean = np.tile(mean, (pop_size, 1))
        return duplicated_mean + (step_size * mutations)

    @staticmethod
    def update_mean(mean, step_size, parent):
        # returns mean of a new population as array (n)
        return mean + step_size*parent

    @staticmethod
    def cumulation_for_cm(path_cm, c_c, path_ss, n, mu_w, parent):
        # returns cumulated evolution path of covariance matrix
        path_cm = path_cm * (1-c_c)
        if(np.linalg.norm(path_ss) < 1.5 * np.sqrt(n)):
            path_cm = path_cm + (np.sqrt(1 - (1-c_c)**2) * np.sqrt(mu_w) * parent)
        return path_cm

    @staticmethod
    def cumulation_for_ss(path_ss, c_sigma, mu_w, cov_matrix, parent):
        # returns cumulated evolution path of step-size
        cm_eigen_dec = __class__.modify_eigen_decomposition(cov_matrix)
        return (1-c_sigma) * path_ss + np.sqrt(1 - (1-c_sigma)**2) * np.sqrt(mu_w) * cm_eigen_dec @ parent

    @staticmethod
    def update_cm(cov_matrix, c_1, c_mu, path_cm, cm_mu):
        # returns covariance matrix of a new population (pop_size, pop_size)
        return (1 - c_1 - c_mu) * cov_matrix + c_1 * path_cm * path_cm.T + c_mu * cm_mu

    @staticmethod
    def update_ss(step_size, c_sigma, d_sigma, path_ss):
        # returns step-size of a new population
        factor = np.exp((c_sigma / d_sigma) * ((np.linalg.norm(path_ss) / np.sqrt(np.shape(path_ss)[0])) - 1))
        return step_size * factor

    @staticmethod
    def fitness(challenges, challenge_num, measured_rels, individuals):
        # returns individuals sorted by their fitness (correlation coefficient)
        pop_size = np.shape(individuals)[0]
        built_LTFArrays = __class__.build_LTFArrays(individuals[:, :-1])
        delay_diffs = __class__.get_delay_differences(built_LTFArrays, pop_size, challenges, challenge_num)
        #print('delay_diffs:\n', delay_diffs)
        epsilons = individuals[:, -1]
        #print('epsilons:\n', epsilons)
        reliabilities = __class__.get_reliabilities(delay_diffs, epsilons)
        print('reliabilities:\n', reliabilities)
        correlations = __class__.get_correlations(reliabilities, measured_rels)
        print('correlations:\n', correlations)
        return correlations

    @staticmethod
    def sort_individuals(individuals, correlations):
        # returns 2D array of individuals as given from input, but sorted through
        #   correlation coefficients
        sorted_indices = np.argsort(correlations)
        return individuals[sorted_indices[::-1]]


    # methods for calculating fitness
    @staticmethod
    def build_LTFArrays(individuals):
        # returns iterator over ltf_arrays created out of every individual
        pop_size = np.shape(individuals)[0]
        for i in range(pop_size):
            yield LTFArray(individuals[i, np.newaxis, :], LTFArray.transform_atf, LTFArray.combiner_xor, bias=False)

    @staticmethod
    def get_delay_differences(built_LTFArrays, pop_size, challenges, challenge_num):
        # returns 2D array of delay differences for all challenges on every
        #   individual
        delay_diffs = np.empty((pop_size, challenge_num))
        for i, built_LTFArray in enumerate(built_LTFArrays):
            delay_diffs[i, :] = built_LTFArray.val(challenges)
        return delay_diffs

    @staticmethod
    def get_reliabilities(delay_diffs, epsilons):
        # returns 2D array of reliabilities for all challenges on every individual
        reliabilities = np.zeros(np.shape(delay_diffs))
        for i in range(np.shape(epsilons)[0]):
            indices_of_reliable = np.abs(delay_diffs[i, :]) > np.abs(epsilons[i])
            reliabilities[i, indices_of_reliable] = 1
        return reliabilities

    @staticmethod
    def get_correlations(reliabilities, measured_rels):
        # returns array of pearson correlation coefficients between reliability
        #   array of individual and instance for all individuals
        pop_size = np.shape(reliabilities)[0]
        correlations = np.zeros(pop_size)
        for i in range(pop_size):
            if np.any(reliabilities[i, :]):
                correlations[i] = np.corrcoef(reliabilities[i, :], measured_rels)[0, 1]
        return correlations


    # helping methods
    @staticmethod
    def set_pole_of_LTFs(instance, different_LTFs):
        # returns the correctly polarized XOR-LTFArray
        challenge_num = 10
        responses = np.empty((2, challenge_num))
        challenges = tools.sample_inputs(instance.n, challenge_num)
        cs1, cs2 = itertools.tee(challenges)
        xor_LTFArray = LTFArray(different_LTFs, LTFArray.transform_id, LTFArray.combiner_xor)
        responses[0, :] = instance.eval(cs1)
        responses[1, :] = xor_LTFArray.eval(cs2)
        difference = np.sum(np.abs(responses[0, :] - responses[1, :]))
        if difference > challenge_num:
            different_LTFs[0, :] *= -1
        return different_LTFs

    @staticmethod
    def is_correlated(responses):
        # returns True iff 2 response arrays are more than 75% equal
        (num_of_LTFs, challenge_num) = np.shape(responses)
        for i in range(1, num_of_LTFs):
            differences = np.sum(np.abs(responses[0, :] - responses[i, :])) / 2
            if differences < 0.25*challenge_num or differences > 0.75*challenge_num:
                return True
        return False

    @staticmethod
    def measure_rels(instance, challenges, challenge_num, repeat):
        # returns array of measured reliabilities of instance
        responses = np.empty((repeat, challenge_num))
        for i in range(repeat):
            responses[i, :] = instance.eval(challenges)
        return np.abs(np.sum(responses, axis=0)) / repeat

    @staticmethod
    def get_parent(sorted_individuals, parent_size, priorities):
        # returns the weighted sum of the fittest individuals
        parent = np.zeros(np.shape(sorted_individuals)[1])
        for i in range(parent_size):
            parent = parent + priorities[i] * sorted_individuals[i, :]
        return parent

    @staticmethod
    def get_cm_mu(sorted_individuals, parent_size, priorities):
        # returns the weighted sum of the fittest individuals
        cm_mu = np.zeros((np.shape(sorted_individuals)[1], np.shape(sorted_individuals)[1]))
        for i in range(parent_size):
            cm_mu = cm_mu + priorities[i] * sorted_individuals[i, :, np.newaxis] @ sorted_individuals[i, np.newaxis, :]
        return cm_mu

    @staticmethod
    def modify_eigen_decomposition(matrix):
        # returns modified eigen-decomposition of matrix A = B * D^2 * B^T
        #   B * D^(-1) * B^T
        eigen_values, eigen_vectors = np.linalg.eigh(matrix)
        diagonal = np.sqrt((np.diag(eigen_values)))
        diagonal_inverse = np.linalg.inv(diagonal)
        return eigen_vectors @ diagonal_inverse @ eigen_vectors.T