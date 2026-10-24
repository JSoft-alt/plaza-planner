"""Keep service plazas; reject big malls, big-box destinations and their interiors."""
import unittest
from copy import deepcopy
from test_planner import config,fixture
import discovery


class RetailDestinations(unittest.TestCase):
    def sample(self):
        data=fixture()[:6]
        data=data[:1]+data[2:]
        data[0]['tags']={'shop':'mall','name':'Neighborhood Service Plaza'}
        return data

    def test_small_service_plaza_keeps_barber_and_laundry(self):
        results=discovery.qualify(self.sample(),config(diameter_miles=1))
        self.assertEqual(len(results),1)
        self.assertEqual(results[0]['shop_count'],2)

    def test_explicit_big_mall_and_big_box_destinations_are_excluded(self):
        for name in ['American Dream','Americana Manhasset',"Macy's",'Macys','Target','Walmart','Home Depot',"Lowe's",'Lowes',"Sam's Club",'Sams Club']:
            with self.subTest(name=name):
                data=self.sample();data[0]['tags']['name']=name
                self.assertEqual(discovery.qualify(data,config(diameter_miles=1)),[])

    def test_large_indoor_mall_does_not_reappear_as_an_internal_service_plaza(self):
        data=self.sample();outer=deepcopy(data[0]);outer['id']=99
        outer['tags']={'shop':'mall','name':'American Dream'}
        data.insert(0,outer)
        self.assertEqual(discovery.qualify(data,config(diameter_miles=1)),[])

    def test_multi_floor_mall_is_excluded_even_when_not_named_in_list(self):
        data=self.sample();data[0]['tags']['building:levels']='4'
        self.assertEqual(discovery.qualify(data,config(diameter_miles=1)),[])
