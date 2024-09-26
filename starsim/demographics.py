"""
Define pregnancy, deaths, migration, etc.
"""

import numpy as np
import starsim as ss
import sciris as sc
import pandas as pd

ss_float_ = ss.dtypes.float
ss_int_ = ss.dtypes.int

__all__ = ['Demographics', 'Births', 'Deaths', 'Pregnancy', 'PregnancyLite']


class Demographics(ss.Module):
    """
    A demographic module typically handles births/deaths/migration and takes
    place at the start of the timestep, before networks are updated and before
    any disease modules are executed.
    """
    def init_pre(self, sim):
        super().init_pre(sim)
        return

    def init_results(self):
        pass


class Births(Demographics):
    """ Create births based on rates, rather than based on pregnancy """
    def __init__(self, pars=None, metadata=None, **kwargs):
        super().__init__()
        self.define_pars(
            birth_rate = 30,
            rel_birth = 1,
            units = 1e-3,  # assumes birth rates are per 1000. If using percentages, switch this to 1
        )
        self.update_pars(pars, **kwargs)

        # Process metadata. Defaults here are the labels used by UN data
        self.metadata = sc.mergedicts(
            sc.objdict(data_cols=dict(year='Year', value='CBR')),
            metadata,
        )

        # Process data, which may be provided as a number, dict, dataframe, or series
        # If it's a number it's left as-is; otherwise it's converted to a dataframe
        self.pars.birth_rate = self.standardize_birth_data()
        self.n_births = 0 # For results tracking
        return

    def init_pre(self, sim):
        """ Initialize with sim information """
        super().init_pre(sim)
        if isinstance(self.pars.birth_rate, pd.DataFrame):
            br_year = self.pars.birth_rate[self.metadata.data_cols['year']]
            br_val = self.pars.birth_rate[self.metadata.data_cols['cbr']]
            all_birth_rates = np.interp(self.timevec, br_year, br_val) # This assumes a year timestep -- probably ok?
            self.pars.birth_rate = all_birth_rates
        return

    def standardize_birth_data(self):
        """ Standardize/validate birth rates - handled in an external file due to shared functionality """
        birth_rate = ss.standardize_data(data=self.pars.birth_rate, metadata=self.metadata)
        if isinstance(birth_rate, (pd.Series, pd.DataFrame)):
            return birth_rate.xs(0,level='age')
        return birth_rate

    def init_results(self):
        self.results += [
            ss.Result(self.name, 'new',        self.npts, dtype=int, scale=True,    label='New births'),
            ss.Result(self.name, 'cumulative', self.npts, dtype=int, scale=True,    label='Cumulative births'),
            ss.Result(self.name, 'cbr',        self.npts, dtype=float, scale=False, label='Crude birth rate'),
        ]
        return

    def get_births(self):
        """
        Extract the right birth rates to use and translate it into a number of people to add.
        """
        sim = self.sim
        p = self.pars

        if isinstance(p.birth_rate, (pd.Series, pd.DataFrame)):
            available_years = p.birth_rate.index
            year_ind = sc.findnearest(available_years, sim.now) # TODO: make robust to timestep
            nearest_year = available_years[year_ind]
            this_birth_rate = p.birth_rate.loc[nearest_year]
        else:
            this_birth_rate = p.birth_rate

        scaled_birth_prob = this_birth_rate * p.units * p.rel_birth * sim.pars.dt
        scaled_birth_prob = np.clip(scaled_birth_prob, a_min=0, a_max=1)
        n_new = int(np.floor(sim.people.alive.count() * scaled_birth_prob))
        return n_new

    def step(self):
        new_uids = self.add_births()
        self.n_births = len(new_uids)
        return new_uids

    def add_births(self):
        """ Add n_new births to each state in the sim """
        people = self.sim.people
        n_new = self.get_births()
        new_uids = people.grow(n_new)
        people.age[new_uids] = 0
        return new_uids

    def update_results(self):
        self.results['new'][self.ti] = self.n_births
        return

    def finalize(self):
        super().finalize()
        res = self.sim.results
        self.results.cumulative[:] = np.cumsum(self.results.new)
        self.results.cbr[:] = 1/self.pars.units*np.divide(self.results.new/self.sim.dt_year, res.n_alive, where=res.n_alive>0)
        return


class Deaths(Demographics):
    def __init__(self, pars=None, metadata=None, **kwargs):
        """
        Configure disease-independent "background" deaths.

        The probability of death for each agent on each timestep is determined
        by the `death_rate` parameter and the time step. The default value of
        this parameter is 0.02, indicating that all agents will
        face a 2% chance of death per year.

        However, this function can be made more realistic by using a dataframe
        for the `death_rate` parameter, to allow it to vary by year, sex, and
        age.  The separate 'metadata' argument can be used to configure the
        details of the input datafile.

        Alternatively, it is possible to override the `death_rate` parameter
        with a bernoulli distribution containing a constant value of function of
        your own design.

        Args:
            pars: dict with arguments including:
                rel_death: constant used to scale all death rates
                death_rate: float, dict, or pandas dataframe/series containing mortality data
                units: units for death rates (see in-line comment on par dict below)

            metadata: data about the data contained within the data input.
                "data_cols" is is a dictionary mapping standard keys, like "year" to the
                corresponding column name in data. Similar for "sex_keys". Finally,
        """
        super().__init__()
        self.define_pars(
            rel_death = 1,
            death_rate = 20,  # Default = a fixed rate of 2%/year, overwritten if data provided
            units = 1e-3,  # assumes death rates are per 1000. If using percentages, switch this to 1
        )
        self.update_pars(pars, **kwargs)

        # Process metadata. Defaults here are the labels used by UN data
        self.metadata = sc.mergedicts(
            sc.objdict(
                data_cols = dict(year='Time', sex='Sex', age='AgeGrpStart', value='mx'),
                sex_keys = {'Female':'f', 'Male':'m'},
            ),
            metadata
        )

        # Process data, which may be provided as a number, dict, dataframe, or series
        # If it's a number it's left as-is; otherwise it's converted to a dataframe
        self.death_rate_data = self.standardize_death_data() # TODO: refactor
        self.pars.death_rate = ss.bernoulli(p=self.make_death_prob_fn)
        self.n_deaths = 0 # For results tracking
        return
    
    def standardize_death_data(self):
        """ Standardize/validate death rates - handled in an external file due to shared functionality """
        death_rate = ss.standardize_data(data=self.pars.death_rate, metadata=self.metadata)
        if isinstance(death_rate, (pd.Series, pd.DataFrame)):
            death_rate = death_rate.unstack(level='age')
            assert not death_rate.isna().any(axis=None) # For efficiency, we assume that the age bins are the same for all years in the input dataset
        return death_rate

    @staticmethod # Needs to be static since called externally, although it sure looks like a class method!
    def make_death_prob_fn(self, sim, uids):
        """ Take in the module, sim, and uids, and return the probability of death for each UID on this timestep """

        drd = self.death_rate_data
        if sc.isnumber(drd):
            death_rate = drd
        else:

            ppl = sim.people

            # Performance optimization - the Deaths module checks for deaths for all agents
            # Therefore the UIDs requested should match all UIDs
            assert len(uids) == len(ppl.auids)

            available_years = drd.index.get_level_values('year')
            year_ind = sc.findnearest(available_years, sim.now) # TODO: make work with different timesteps
            nearest_year = available_years[year_ind]

            death_rate = np.empty(uids.shape, dtype=ss_float_)

            if 'sex' in drd.index.names:
                s = drd.loc[nearest_year, 'f']
                binned_ages = np.digitize(ppl.age[ppl.female], s.index)-1 # Negative ages will be in the first bin - do *not* subtract 1 so that this bin is 0
                death_rate[ppl.female] = s.values[binned_ages]
                s = drd.loc[nearest_year, 'm']
                binned_ages = np.digitize(ppl.age[ppl.male], s.index)-1 # Negative ages will be in the first bin - do *not* subtract 1 so that this bin is 0
                death_rate[ppl.male] = s.values[binned_ages]
            else:
                s = drd.loc[nearest_year]
                binned_ages = np.digitize(ppl.age, s.index)-1 # Negative ages will be in the first bin - do *not* subtract 1 so that this bin is 0
                death_rate[:] = s.values[binned_ages]

        # Scale from rate to probability. Consider an exponential here.
        death_prob = death_rate * (self.pars.units * self.pars.rel_death * sim.pars.dt)
        death_prob = np.clip(death_prob, a_min=0, a_max=1)

        return death_prob

    def init_results(self):
        self.results += [
            ss.Result(self.name, 'new',        self.npts, dtype=int, scale=True,  label='Deaths'),
            ss.Result(self.name, 'cumulative', self.npts, dtype=int, scale=True,  label='Cumulative deaths'),
            ss.Result(self.name, 'cmr',        self.npts, dtype=int, scale=False, label='Crude mortality rate'),
        ]
        return

    def step(self):
        """ Select people to die """
        death_uids = self.pars.death_rate.filter()
        self.sim.people.request_death(death_uids)
        self.n_deaths = len(death_uids)
        return self.n_deaths
    
    def update_results(self):
        self.results['new'][self.ti] = self.n_deaths
        return

    def finalize(self):
        super().finalize()
        n_alive = self.sim.results.n_alive
        self.results.cumulative[:] = np.cumsum(self.results.new)
        self.results.cmr[:] = 1/self.pars.units*np.divide(self.results.new / self.sim.dt_year, n_alive, where=n_alive>0)
        return


class Pregnancy(Demographics):
    """ Create births via pregnancies """
    def __init__(self, pars=None, metadata=None, **kwargs):
        super().__init__()
        self.define_pars(
            dur_pregnancy = ss.years(0.75), # Duration for pre-natal transmission
            dur_postpartum = ss.lognorm_ex(mean=ss.years(0.5), std=ss.years(0.5)), # Duration for post-natal transmission (e.g. via breastfeeding)
            fertility_rate = 0, # Can be a number of Pandas DataFrame
            rel_fertility = 1,
            maternal_death_prob = ss.bernoulli(0),
            p_neonataldeath_on_maternaldeath = ss.bernoulli(0),
            sex_ratio = ss.bernoulli(0.5), # Ratio of babies born female
            min_age = 15, # Minimum age to become pregnant
            max_age = 50, # Maximum age to become pregnant
            units = 1e-3, # Assumes fertility rates are per 1000. If using percentages, switch this to 1
            burnin = True, # Should we seed pregnancies that would have happened before the start of the simulation?
        )
        self.update_pars(pars, **kwargs)

        self.pars.p_fertility = ss.bernoulli(p=0) # Placeholder, see make_fertility_prob_fn
        
        # Other, e.g. postpartum, on contraception...
        self.define_states(
            ss.BoolArr('infertile', label='Infertile'),  # Applies to girls and women outside the fertility window
            ss.BoolArr('fecund', default=True, label='Female of childbearing age'),
            ss.BoolArr('pregnant', label='Pregnant'),  # Currently pregnant
            ss.BoolArr('postpartum', label="Post-partum"),  # Currently post-partum
            ss.FloatArr('child_uid', label='UID of children, from embryo through postpartum'),
            ss.FloatArr('dur_postpartum', label='Post-partum duration'),  # Duration of postpartum phase
            ss.FloatArr('ti_pregnant', label='Time of pregnancy'),  # Time pregnancy begins
            ss.FloatArr('ti_delivery', label='Time of delivery'),  # Time of delivery
            ss.FloatArr('ti_postpartum', label='Time post-partum ends'),  # Time postpartum ends
            ss.FloatArr('ti_dead', label='Time of maternal death'),  # Maternal mortality
        )

        # Process metadata. Defaults here are the labels used by UN data
        self.metadata = sc.mergedicts(
            sc.objdict(data_cols=dict(year='Time', age='AgeGrp', value='ASFR')),
            metadata,
        )
        self.choose_slots = None # Distribution for choosing slots; set in self.init()

        # For results tracking
        self.n_pregnancies = 0
        self.n_births = 0
        return

    @staticmethod
    def make_fertility_prob_fn(self, sim, uids):
        """ Take in the module, sim, and uids, and return the conception probability for each UID on this timestep """

        age = sim.people.age[uids]

        frd = self.fertility_rate_data
        fertility_rate = np.zeros(len(sim.people.uid.raw), dtype=ss_float_)

        if sc.isnumber(frd):
            fertility_rate[uids] = self.fertility_rate_data
        else:
            year_ind = sc.findnearest(frd.index, sim.now-self.pars.dur_pregnancy) # TODO: make time-unit-aware
            nearest_year = frd.index[year_ind]

            # Assign agents to age bins
            age_bins = self.fertility_rate_data.columns.values
            age_bin_all = np.digitize(age, age_bins) - 1
            new_rate = self.fertility_rate_data.loc[nearest_year].values.copy()  # Initialize array with new rates

            if self.pregnant.any():
                # Scale the new rate to convert the denominator from all women to non-pregnant women
                v, c = np.unique(age_bin_all, return_counts=True)
                age_counts = np.zeros(len(age_bins))
                age_counts[v] = c

                age_bin_pw = np.digitize(sim.people.age[self.pregnant], age_bins) - 1
                v, c = np.unique(age_bin_pw, return_counts=True)
                pregnant_age_counts = np.zeros(len(age_bins))
                pregnant_age_counts[v] = c

                num_to_make = new_rate * age_counts  # Number that we need to make pregnant
                new_denom = age_counts - pregnant_age_counts  # New denominator for rates
                np.divide(num_to_make, new_denom, where=new_denom>0, out=new_rate)

            fertility_rate[uids] = new_rate[age_bin_all]

        # Scale from rate to probability
        invalid_age = (age < self.pars.min_age) | (age > self.pars.max_age)
        fertility_prob = fertility_rate * (self.pars.units * self.pars.rel_fertility * sim.pars.dt)
        fertility_prob[(~self.fecund).uids] = 0 # Currently infecund women cannot become pregnant
        fertility_prob[self.infertile.uids] = 0 # Do not allow infertile 
        fertility_prob[uids[invalid_age]] = 0 # Women too young or old cannot become pregnant
        fertility_prob = np.clip(fertility_prob[uids], a_min=0, a_max=1)
        return fertility_prob

    def standardize_fertility_data(self):
        """
        Standardize/validate fertility rates
        """
        fertility_rate = ss.standardize_data(data=self.pars.fertility_rate, metadata=self.metadata)
        if isinstance(fertility_rate, (pd.Series, pd.DataFrame)):
            fertility_rate = fertility_rate.unstack()
            # Interpolate to 1 year increments
            fertility_rate = fertility_rate.reindex(np.arange(fertility_rate.index.min(), fertility_rate.index.max() + 1)).interpolate()
            max_age = fertility_rate.columns.max()
            fertility_rate[max_age + 1] = 0
            assert not fertility_rate.isna().any(axis=None) # For efficiency, we assume that the age bins are the same for all years in the input dataset
        return fertility_rate

    def init_pre(self, sim):
        super().init_pre(sim)

        # Process data, which may be provided as a number, dict, dataframe, or series
        # If it's a number it's left as-is; otherwise it's converted to a dataframe
        self.fertility_rate_data = self.standardize_fertility_data()
        self.pars.p_fertility.set(p=self.make_fertility_prob_fn)

        low = sim.pars.n_agents + 1
        high = int(sim.pars.slot_scale*sim.pars.n_agents)
        high = np.maximum(high, sim.pars.min_slots) # Make sure there are at least min_slots slots to avoid artifacts related to small populations
        self.choose_slots = ss.randint(low=low, high=high, sim=sim, module=self)
        return

    def init_results(self):
        """
        Results could include a range of birth outcomes e.g. LGA, stillbirths, etc.
        Still unclear whether this logic should live in the pregnancy module, the
        individual disease modules, the connectors, or the sim.
        """
        self.results += [
            ss.Result(self.name, 'pregnancies', self.npts, dtype=int, scale=True,    label='New pregnancies'),
            ss.Result(self.name, 'births',      self.npts, dtype=int, scale=True,    label='New births'),
            ss.Result(self.name, 'cbr',         self.npts, dtype=float, scale=False, label='Crude birth rate'),
        ]
        return

    def update(self):
        if self.sim.ti == 0 and self.pars.burnin:
            dtis = np.arange(np.ceil(-1 * self.pars.dur_pregnancy / self.sim.dt), 0, 1).astype(int)
            for dti in dtis:
                self.sim.ti = dti
                self.do_update()
            self.sim.ti = 0
        new_uids = self.do_update()

        return new_uids

    def do_update(self):
        """ Perform all updates """
        self.update_states()
        conceive_uids = self.make_pregnancies()
        self.n_pregnancies = len(conceive_uids)
        new_uids = self.make_embryos(conceive_uids)
        return new_uids

    def update_states(self):
        """ Update states """
        # Check for new deliveries
        ti = self.ti
        deliveries = self.pregnant & (self.ti_delivery <= ti)
        self.n_births = np.count_nonzero(deliveries)
        self.pregnant[deliveries] = False
        self.postpartum[deliveries] = True
        self.fecund[deliveries] = False

        # Add connections to any postnatal transmission layers
        for lkey, layer in self.sim.networks.items():
            if layer.postnatal and self.n_births:

                # Add postnatal connections by finding the prenatal contacts
                # Validation of the networks is done during initialization to ensure that 1 prenatal netwrok is present
                prenatalnet = [nw for nw in self.sim.networks.values() if nw.prenatal][0]

                # Find the prenatal connections that are ending
                prenatal_ending = prenatalnet.edges.end <= ti
                new_mother_uids = prenatalnet.edges.p1[prenatal_ending]
                new_infant_uids = prenatalnet.edges.p2[prenatal_ending]

                # Validation
                if not np.array_equal(new_mother_uids, deliveries.uids):
                    errormsg = 'IDs of new mothers do not match IDs of new deliveries'
                    raise ValueError(errormsg)

                # Create durations and start dates, and add connections
                durs = self.dur_postpartum[new_mother_uids]
                start = np.full(self.n_births, fill_value=ti)

                # # Remove pairs from prenatal network and add to postnatal
                prenatalnet.end_pairs()
                layer.add_pairs(new_mother_uids, new_infant_uids, dur=durs, start=start)

        # Check for new women emerging from post-partum
        postpartum = self.postpartum & (self.ti_postpartum <= ti)
        self.postpartum[postpartum] = False
        self.fecund[postpartum] = True
        self.child_uid[postpartum] = np.nan

        # Maternal deaths
        maternal_deaths = (self.ti_dead <= ti).uids
        self.sim.people.request_death(maternal_deaths)
        return

    def make_pregnancies(self):
        """ Select people to make pregnant using incidence data """
        # People eligible to become pregnant. We don't remove pregnant people here, these
        # are instead handled in the fertility_dist logic as the rates need to be adjusted
        eligible_uids = self.sim.people.female.uids
        conceive_uids = self.pars.p_fertility.filter(eligible_uids)

        # Validation
        if np.any(self.pregnant[conceive_uids]):
            which_uids = conceive_uids[self.pregnant[conceive_uids]]
            errormsg = f'New conceptions registered in {len(which_uids)} pregnant agent(s) at timestep {self.ti}.'
            raise ValueError(errormsg)

        # Set prognoses for the pregnancies
        if len(conceive_uids) > 0:
            self.set_prognoses(conceive_uids)
        return conceive_uids

    def make_embryos(self, conceive_uids):
        """ Add properties for the just-conceived """
        people = self.sim.people
        n_unborn = len(conceive_uids)
        if n_unborn == 0:
            new_uids = ss.uids()
        else:

            # Choose slots for the unborn agents
            new_slots = self.choose_slots.rvs(conceive_uids)

            # Grow the arrays and set properties for the unborn agents
            new_uids = people.grow(len(new_slots), new_slots)
            people.age[new_uids] = -self.pars.dur_pregnancy
            people.slot[new_uids] = new_slots  # Before sampling female_dist
            people.female[new_uids] = self.pars.sex_ratio.rvs(conceive_uids)
            people.parent[new_uids] = conceive_uids
            self.child_uid[conceive_uids] = new_uids

            # Add connections to any prenatal transmission layers
            for lkey, layer in self.sim.networks.items():
                if layer.prenatal:
                    durs = np.full(n_unborn, fill_value=self.pars.dur_pregnancy)
                    start = np.full(n_unborn, fill_value=self.ti)
                    layer.add_pairs(conceive_uids, new_uids, dur=durs, start=start)

        if self.sim.ti < 0:
            people.age[new_uids] += -self.sim.ti * self.sim.dt # Age to ti=0

        return new_uids

    def set_prognoses(self, uids):
        """
        Make pregnancies
        Add miscarriage/termination logic here
        Also reconciliation with birth rates
        Q, is this also a good place to check for other conditions and set prognoses for the fetus?
        """

        # Change states for the newly pregnant woman
        ti = self.ti
        dt = self.dt # TODO: CHECK
        self.fecund[uids] = False
        self.pregnant[uids] = True
        self.ti_pregnant[uids] = ti

        # Outcomes for pregnancies
        dur_preg = np.ones(len(uids))*self.pars.dur_pregnancy  # Duration in years
        dur_postpartum = self.pars.dur_postpartum.rvs(uids)
        dead = self.pars.maternal_death_prob.rvs(uids)
        self.ti_delivery[uids] = ti + dur_preg # Currently assumes maternal deaths still result in a live baby
        self.ti_postpartum[uids] = self.ti_delivery[uids] + dur_postpartum
        self.dur_postpartum[uids] = dur_postpartum

        if np.any(dead): # NB: 100x faster than np.sum(), 10x faster than np.count_nonzero()
            self.ti_dead[uids[dead]] = ti + dur_preg[dead]
        return

    def update_death(self, death_uids):
        # Any pregnant? Consider death of the neonate
        mother_uids = death_uids[self.pregnant[death_uids]]
        if len(mother_uids):
            neonate_uids = ss.uids(self.child_uid[mother_uids])
            neonataldeath_uids = self.pars.p_neonataldeath_on_maternaldeath.filter(neonate_uids)
            if len(neonataldeath_uids):
                self.sim.people.request_death(neonataldeath_uids)

        # Any prenatal? Handle changes to pregnancy
        is_prenatal = self.sim.people.age[death_uids] < 0
        neonate_uids = death_uids[is_prenatal]
        if len(neonate_uids):
            mother_uids = self.sim.people.parent[neonate_uids]
            # Baby lost, mother no longer pregnant
            self.pregnant[mother_uids] = False
            self.fecund[mother_uids] = True # Or wait?
            self.postpartum[mother_uids] = False
            self.child_uid[mother_uids] = np.nan
            self.ti_delivery[mother_uids] = np.nan
            self.ti_postpartum[mother_uids] = np.nan
            # Keep ti_dead
        return

    def update_results(self):
        ti = self.ti
        self.results['pregnancies'][ti] = self.n_pregnancies
        self.results['births'][ti] = self.n_births
        return

    def finalize(self):
        super().finalize()
        n_alive = self.sim.results.n_alive
        self.results['cbr'] = 1/self.pars.units * np.divide(self.results['births'] / self.sim.dt_year, n_alive, where=n_alive>0)
        return


class PregnancyLite(Demographics):
    """
    Create births via pregnancies
    
    Similar to the full Pregnancy module with the following difference (for performance):
    * Does not create vertical connections between mother and child in the Maternal network, and thus is not compatible with vertical transmisison.
    * Does not adjust fertility rates for pregnancy
    * Does not include maternal mortality linked to pregnancy
    """

    def __init__(self, pars=None, metadata=None, **kwargs):
        super().__init__()
        self.default_pars(
            dur_pregnancy = 0.75, # Duration for pre-natal period
            dur_postpartum = ss.lognorm_ex(0.5, 0.5), # Duration for post-natal transmission (e.g. via breastfeeding)
            fertility_rate = 25,
            units = 1e-3, # Assumes fertility rates are per 1000. If using percentages, switch this to 1
            rel_fertility = 1,
            sex_ratio = ss.bernoulli(0.5), # Ratio of babies born female
            min_age = 15, # Minimum age to become pregnant
            max_age = 50, # Maximum age to become pregnant
            burnin = True, # Should we seed pregnancies that would have happened before the start of the simulation?
        )
        self.update_pars(pars, **kwargs)

        self.p_fertility = ss.bernoulli(p=0) # Placeholder, see make_fertility_prob_fn
        
        # Other, e.g. postpartum, on contraception...
        self.add_states(
            ss.BoolArr('pregnant', label='Pregnant'),  # Currently pregnant
            ss.BoolArr('postpartum', label="Post-partum"),  # Currently post-partum
            ss.FloatArr('child_uid', label='UID of children, from embryo through postpartum'),
            ss.FloatArr('ti_delivery', label='Time of delivery'),  # Time of delivery
            ss.FloatArr('ti_postpartum', label='Time post-partum ends'),  # Time postpartum ends
        )

        # Process metadata. Defaults here are the labels used by UN data
        self.metadata = sc.mergedicts(
            sc.objdict(data_cols=dict(year='Time', age='AgeGrp', value='ASFR')),
            metadata,
        )
        self.choose_slots = None # Distribution for choosing slots; set in self.initialize()

        # For results tracking
        self.n_pregnancies = 0
        self.n_births = 0
        return

    @staticmethod
    def time_varying_ASFR(self, sim, uids):
        """ Take in the module, sim, and uids, and return the conception probability for each UID on this timestep """

        age = sim.people.age[uids]
        p_conception = np.zeros(len(uids), dtype=ss_float_)

        # Careful with year due to negative ti during initialization
        year = self.sim.yearvec[0] + self.sim.ti * self.sim.dt
        yi = np.argmin(year+self.pars.dur_pregnancy > self.frd.index)
        if yi > 0:
            yi -= 1 # So sim.year-self.pars.dur_pregnancy is in the current data year
        ASFR = self.frd.iloc[yi]

        age_bin = np.digitize(age, ASFR.index) - 1
        p_conception = ASFR.values[age_bin] * self.pars.units * self.pars.rel_fertility * sim.pars.dt
        return p_conception

    def init_pre(self, sim):
        super().init_pre(sim)

        if np.isscalar(self.pars.fertility_rate):
            self.p_fertility.set(p=self.fertility_rate * sim.dt)
        else:
            assert set(self.pars.fertility_rate.columns).issuperset(set(['Time', 'AgeGrp', 'ASFR']))
            self.frd = self.pars.fertility_rate.pivot(index='Time', columns='AgeGrp', values='ASFR')
            self.p_fertility.set(p=self.time_varying_ASFR)

        low = sim.pars.n_agents + 1
        high = int(sim.pars.slot_scale*sim.pars.n_agents)
        high = np.maximum(high, sim.pars.min_slots) # Make sure there are at least min_slots slots to avoid artifacts related to small populations
        self.choose_slots = ss.randint(low=low, high=high, sim=sim, module=self)
        return

    def init_results(self):
        """
        Results could include a range of birth outcomes e.g. LGA, stillbirths, etc.
        Still unclear whether this logic should live in the pregnancy module, the
        individual disease modules, the connectors, or the sim.
        """
        npts = self.sim.npts
        self.results += [
            ss.Result(self.name, 'pregnancies', npts, dtype=int, scale=True, label='New pregnancies'),
            ss.Result(self.name, 'births', npts, dtype=int, scale=True, label='New births'),
            ss.Result(self.name, 'cbr', npts, dtype=float, scale=False, label='Crude birth rate'),
            ss.Result(self.name, 'n_pregnant', npts, dtype=int, scale=False, label='Num pregnant'),
            ss.Result(self.name, 'n_postpartum', npts, dtype=int, scale=False, label='Num postpartum'),
        ]
        return

    def update(self):
        if self.sim.ti == 0 and self.pars.burnin:
            dtis = np.arange(np.ceil(-1 * self.pars.dur_pregnancy / self.sim.dt), 0, 1).astype(int)
            for dti in dtis:
                self.sim.ti = dti
                self.do_update()
            self.sim.ti = 0
        new_uids = self.do_update()

        return new_uids

    def do_update(self):
        """ Perform all updates """
        self.update_states()
        conceive_uids = self.make_pregnancies()
        self.n_pregnancies = len(conceive_uids)
        new_uids = self.make_embryos(conceive_uids)
        return new_uids

    def update_states(self):
        """ Update states """
        # Check for new deliveries
        ti = self.sim.ti
        deliveries = self.pregnant & (self.ti_delivery <= ti)
        self.n_births = np.count_nonzero(deliveries)
        self.pregnant[deliveries] = False
        self.postpartum[deliveries] = True

        # Check for new women emerging from post-partum
        postpartum = self.postpartum & (self.ti_postpartum <= ti)
        self.postpartum[postpartum] = False
        self.child_uid[postpartum] = np.nan

        return

    def make_pregnancies(self):
        """ Select people to make pregnant using incidence data """
        # People eligible to become pregnant. We don't remove pregnant people here, these
        # are instead handled in the fertility_dist logic as the rates need to be adjusted
        ppl = self.sim.people
        eligible_uids = ss.uids(ppl.female & ~self.pregnant & ~self.postpartum & (ppl.age >= self.pars.min_age) & (ppl.age < self.pars.max_age))
        conceive_uids = self.p_fertility.filter(eligible_uids)

        # Set prognoses for the pregnancies
        if len(conceive_uids) > 0:
            self.set_prognoses(conceive_uids)
        return conceive_uids

    def make_embryos(self, conceive_uids):
        """ Add properties for the just-conceived """
        people = self.sim.people
        n_unborn = len(conceive_uids)
        if n_unborn == 0:
            return ss.uids()

        # Choose slots for the unborn agents
        new_slots = self.choose_slots.rvs(conceive_uids)

        # Grow the arrays and set properties for the unborn agents
        new_uids = people.grow(len(new_slots), new_slots)
        people.age[new_uids] = -self.pars.dur_pregnancy
        people.slot[new_uids] = new_slots  # Before sampling female_dist
        people.female[new_uids] = self.pars.sex_ratio.rvs(conceive_uids)
        people.parent[new_uids] = conceive_uids
        self.child_uid[conceive_uids] = new_uids

        if self.sim.ti < 0: # During burn-in
            people.age[new_uids] += -self.sim.ti * self.sim.dt # Age to ti=0

        return new_uids

    def set_prognoses(self, uids):
        """
        Make pregnancies
        Add miscarriage/termination logic here
        Also reconciliation with birth rates
        Q, is this also a good place to check for other conditions and set prognoses for the fetus?
        """

        # Change states for the newly pregnant woman
        ti = self.sim.ti
        dt = self.sim.dt
        self.pregnant[uids] = True

        # Outcomes for pregnancies
        dur_preg = np.full(len(uids), self.pars.dur_pregnancy)  # Duration in years
        dur_postpartum = self.pars.dur_postpartum.rvs(uids)
        self.ti_delivery[uids] = ti + dur_preg/dt # Currently assumes maternal deaths still result in a live baby
        self.ti_postpartum[uids] = self.ti_delivery[uids] + dur_postpartum/dt

        return

    def update_death(self, death_uids):
        is_prenatal = self.sim.people.age[death_uids] < 0
        neonate_uids = death_uids[is_prenatal]
        if len(neonate_uids):
            mother_uids = self.sim.people.parent[neonate_uids]
            # Baby lost, mother no longer pregnant
            self.pregnant[mother_uids] = False
            self.postpartum[mother_uids] = False
            self.child_uid[mother_uids] = np.nan
            self.ti_delivery[mother_uids] = np.nan
            self.ti_postpartum[mother_uids] = np.nan
            # Keep ti_dead
        return

    def update_results(self):
        ti = self.sim.ti
        self.results['pregnancies'][ti] = self.n_pregnancies
        self.results['births'][ti] = self.n_births
        self.results['n_pregnant'][ti] = self.pregnant.sum()
        self.results['n_postpartum'][ti] = self.postpartum.sum()
        return

    def finalize(self):
        super().finalize()
        n_alive = self.sim.results.n_alive
        self.results['cbr'] = 1/self.pars.units * np.divide(self.results['births'] / self.sim.dt, n_alive, where=n_alive>0)
        return
