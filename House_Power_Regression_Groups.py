import requests
from   nordpool import elspot, elbas
import json
from   datetime import timezone
from   datetime import date, datetime, timedelta
import sched, time
import pytz
import tzlocal
import matplotlib.pyplot as plt
import numpy as np
import sched, time
import sensibo_client as SC
import sys
import pickle
sys.path.append('/Applications/casadiPython3')
from casadi import *
from casadi.tools import *

plt.close("all")

# Where are we?
Zone = 'Tr.heim'                               # Spot Market : Trondheim
local_timezone=pytz.timezone('Europe/Oslo')    # Time zone   : Oslo

# Which data?
FileName = 'Power_Test_2021-02-23_1_40'
#FileName = 'Power_Test_2021-02-24_0_0'


ParamName = 'Power_Test_2021-02-21_21_0_Model_Fit'

print('Process data file : '+FileName)

f = open(FileName+'.pkl',"rb")
Data = pickle.load(f)
f.close()

print('Process stat. param. file : '+ParamName)

f         = open(ParamName+'.pkl',"rb")
StatParam = pickle.load(f)
f.close()

# Initialize Nordpool spot prices
#prices_spot = elspot.Prices()

## Stuff
Col = ['r','g','m','c']

# Measurement sampling time
SamplingTime = {'Measurement' :   5,   # Sampling time for measurement
                        'HP'  :  60   # Sampling time for HP
              }
########################  Some functions  ########################

def FindIndex(Times,time):
    # Find the time_index of time in Times
    assert(time >= Times[0])
    assert(time <= Times[-1])
    
    time_index = -1
    for k in range(0,len(Times)-1):
 
        if (Times[k]-time)*(Times[k+1]-time) < 0:
            time_index = k
        if (Times[k]-time) == 0:
            time_index = k
        if (Times[k+1]-time) == 0:
            time_index = k+1
        
    if time_index < 0:
        print('Issue to be checked')
        print(time_index)
        print(time)
        sys.exit()
        
    return time_index

def Average(Times,Data,time_start,time_end):

    assert(time_start >= Times[0])
    
    index_start = FindIndex(Times,time_start)
    index_end   = FindIndex(Times,time_end)
    
    Average = 0
    for index in range(index_start,index_end):
        if Times[index] >= time_start and Times[index+1] <= time_end:
            dt  = Times[index+1] - Times[index]
        if Times[index] < time_start and Times[index+1] <= time_end:
            dt  = Times[index+1] - time_start
        if Times[index] >= time_start and Times[index+1] > time_end:
            dt  = time_end - Times[index]
        if Times[index] < time_start and Times[index+1] > time_end:
            dt  = time_end - time_start
        
        Average += Data[index]*dt # Cumulate energy in J
        
    return Average


## Load experimental data

if FileName == 'Power_Test_2021-02-23_1_40':
    GroupList = [
                      ['livingdown','studio'],
                    ['main','studio']
                ]
else:
    if FileName == 'Power_Test_2021-02-24_0_0':
        GroupList = [  ['studio'],
           ['livingdown','main'],
           ['main','studio'],
           ['livingdown','main','studio']
        ]
    else:
        GroupList = Data['GroupList']

#GroupList = Data['GroupList']

AveragingTime = 5#Data['StepDuration']

Pumps = list(Data['HP'].keys())

# Attribute colors
ColHP = {}
indexcol = 0
for HP in Pumps:
    ColHP[HP] = Col[indexcol]
    indexcol += 1


######################## Find beginning and end of measurements ##################

time_start = Data['Power']['Times'][0]

# Find latest time having measurements for all:
time_end = Data['EnergyRT']['Times'][-1]
for pump in Pumps:
    if Data['HP'][pump]['Times'][-1] < time_end:
        time_end = Data['HP'][pump]['Times'][-1]

time_span = (time_end - time_start).total_seconds()/60.

################ Create time grids for all items ################

Times = { 'Power' : [] }
## Average power over AveragingTime
for time in Data['EnergyRT']['Times']:
    Times['Power'].append( (time - time_start).total_seconds()/60.  )
    
for pump in Pumps:
    Times[pump] = []
    for time in Data['HP'][pump]['Times']:
        Times[pump].append( (time - time_start).total_seconds()/60.  )


time_grid = [0]
while time_grid[-1] + AveragingTime <= time_span:
    time_grid.append(time_grid[-1] + AveragingTime)

################ Compute cumulated power in [J] ##############

Energy = [0]
for k in range(len(Data['EnergyRT']['Times'])-1):
    dt      = (Data['EnergyRT']['Times'][k+1] - Data['EnergyRT']['Times'][k]).total_seconds()
    Energy.append( Energy[-1] + Data['EnergyRT']['Power'][k]*dt )
    
################   Average power   ##############

PowerAverage = []
for time in time_grid:
    Energy_start = np.interp(time                , Times['Power'], Energy    )
    Energy_end   = np.interp(time + AveragingTime, Times['Power'], Energy    )
    
    Delta_Energy = (Energy_end - Energy_start)                         # in J
    AvPower      = Delta_Energy/(60*AveragingTime)/1e3     # in kW
    PowerAverage.append( AvPower )


################   Interpolate data  ##############

Temperatures = { 'Meas': {},
                 'Ref' : {}}
DTemp        = {}
PumpOn       = {}
for pump in Pumps:
    Temperatures['Meas'][pump] = np.interp(   time_grid, Times[pump], Data['HP'][pump]['Measurements']['temperature']    )
    Temperatures[ 'Ref'][pump] = np.interp(   time_grid, Times[pump], Data['HP'][pump]['States']['targetTemperature']    )
    PumpOn[pump]               = np.interp(   time_grid, Times[pump], Data['HP'][pump]['States']['on']                   )
    DTemp[pump]                = Temperatures[ 'Ref'][pump] - Temperatures['Meas'][pump]
    
########################  Extract indices when Group on  ####
            
IndicesGroups = []
for group in GroupList:
    IndicesGroup = []
    for k, time in enumerate(time_grid):
        GroupOn = True
        for pump in Pumps:
            if (pump in group) and not(PumpOn[pump][k]):
                GroupOn = False
            if not(pump in group) and PumpOn[pump][k]:
                GroupOn = False
        if GroupOn:
            IndicesGroup.append(k)
    IndicesGroups.append(IndicesGroup)
            
########################  Extract individual pump powers  ########################

DataGroups = []
for index, group in enumerate(GroupList):
    DataGroup = {   'Power' : [],
                    'Time'  : [],
                }
    for pump in group:
        DataGroup[pump] = []
        
    IndicesGroup = IndicesGroups[index]
    for k in IndicesGroup:
        DataGroup['Power'].append(  PowerAverage[k] )
        DataGroup[ 'Time'].append(  time_grid[k]    )
        for pump in group:
            DataGroup[pump].append(  DTemp[pump][k]  )
    
    DataGroups.append(DataGroup)

print('#############  Static nonlinear model  #############')
for pump in Pumps:
    print('HP     : '+pump)
    print('Gain   : '+str(StatParam[pump]['Gain']))
    print('Const. : '+str(StatParam[pump]['Const']))
    print('----------------------------------------------')

########################  Assess single-pump parameters  #############################
Power_groups = []
for index, group in enumerate(GroupList):
    IndicesGroup = IndicesGroups[index]
    Power_group = []
    for k, index_group in enumerate(IndicesGroup):
        Power_model_k = 0
        for pump in group:
            Power_model_k += StatParam[pump]['Gain']*DataGroups[index][pump][k] + StatParam[pump]['Const']
        Power_group.append(Power_model_k)
    Power_groups.append(Power_group)
    
plt.figure(1)
sp = 1
for index, group in enumerate(GroupList):
    plt.subplot(1,len(GroupList),sp)
    for pump in group:
        plt.step(DataGroups[index][ 'Time'],DataGroups[index][pump],linestyle=':',color=ColHP[pump],where='post')

    plt.step(Times['Power'],np.array(Data['EnergyRT']['Power'])/1e3,linestyle='-',color=[.6,.6,.6],where='post')
    plt.step(DataGroups[index][ 'Time'],np.array(DataGroups[index]['Power']),linestyle='-',color='r',where='post')
    plt.step(DataGroups[index][ 'Time'],np.array(Power_groups[index]),linestyle='-',color='b',where='post')
    plt.xlabel('Time')
    plt.ylabel('Power')
    plt.title('Group '+str(index))
    plt.legend(group)
    sp += 1

plt.show(block=False)

