import bisect
import collections
import os
import queue
import random
import subprocess
import threading
import time
import traceback

from hydrus.core import HydrusData
from hydrus.core import HydrusExceptions
from hydrus.core import HydrusGlobals as HG

NEXT_THREAD_CLEAROUT = 0

THREADS_TO_THREAD_INFO = {}
THREAD_INFO_LOCK = threading.Lock()

def CheckIfThreadShuttingDown():
    
    if IsThreadShuttingDown():
        
        raise HydrusExceptions.ShutdownException( 'Thread is shutting down!' )
        
    
def ClearOutDeadThreads():
    
    with THREAD_INFO_LOCK:
        
        all_threads = list( THREADS_TO_THREAD_INFO.keys() )
        
        for thread in all_threads:
            
            if not thread.is_alive():
                
                del THREADS_TO_THREAD_INFO[ thread ]
                
            
        
    
def GetThreadInfo( thread = None ):
    
    global NEXT_THREAD_CLEAROUT
    
    if HydrusData.TimeHasPassed( NEXT_THREAD_CLEAROUT ):
        
        ClearOutDeadThreads()
        
        NEXT_THREAD_CLEAROUT = HydrusData.GetNow() + 600
        
    
    if thread is None:
        
        thread = threading.current_thread()
        
    
    with THREAD_INFO_LOCK:
        
        if thread not in THREADS_TO_THREAD_INFO:
            
            thread_info = {}
            
            thread_info[ 'shutting_down' ] = False
            
            THREADS_TO_THREAD_INFO[ thread ] = thread_info
            
        
        return THREADS_TO_THREAD_INFO[ thread ]
        
    
def IsThreadShuttingDown():
    
    if HG.controller.DoingFastExit():
        
        return True
        
    
    me = threading.current_thread()
    
    if isinstance( me, DAEMON ):
        
        if HG.view_shutdown:
            
            return True
            
        
    else:
        
        if HG.model_shutdown:
            
            return True
            
        
    
    thread_info = GetThreadInfo()
    
    return thread_info[ 'shutting_down' ]
    
def ShutdownThread( thread ):
    
    thread_info = GetThreadInfo( thread )
    
    thread_info[ 'shutting_down' ] = True
    
def SubprocessCommunicate( process: subprocess.Popen ):
    
    def do_test():
        
        if HG.model_shutdown:
            
            try:
                
                process.kill()
                
            except:
                
                pass
                
            
            raise HydrusExceptions.ShutdownException( 'Application is shutting down!' )
            
        
    
    do_test()
    
    while True:
        
        try:
            
            return process.communicate( timeout = 10 )
            
        except subprocess.TimeoutExpired:
            
            do_test()
            
        
    
class DAEMON( threading.Thread ):
    '''
    A thread with extras comperable to a UNIX daemon process, can be suspended and notified of termination.
    Usually used as an abstract base class for other DAEMON* named classes.
    '''
    
    def __init__( self, controller, name ):
        
        threading.Thread.__init__( self, name = name )
        
        self._controller = controller
        self._name = name
        
        self._event = threading.Event()
        
        self._controller.sub( self, 'wake', 'wake_daemons' )
        self._controller.sub( self, 'shutdown', 'shutdown' )
        
    
    def _DoPreCall( self ):
        
        if HG.daemon_report_mode:
            
            HydrusData.ShowText( self._name + ' doing a job.' )
            
        
    
    def GetCurrentJobSummary( self ):
        
        return 'unknown job'
        
    
    def GetName( self ):
        
        return self._name
        
    
    def shutdown( self ):
        
        ShutdownThread( self )
        
        self.wake()
        
    
    def wake( self ):
        
        self._event.set()
        
    
class DAEMONWorker( DAEMON ):
    '''
    A DAEMON thread which checks for work at a set interval.
    '''
    
    def __init__( self, controller, name, callable, topics = None, period = 3600, init_wait = 3, pre_call_wait = 0 ):
        '''
        :param: controller: A reference to the applicaiton controller.
        :param: name: A human redable name for this worker
        :param: callable: The function this worker will call.  The function will have a single argument which takes the HydrusController.
        :param: period: The rate at which this worker will check for work in seconds.'''
        
        if topics is None:
            
            topics = []
            
        
        DAEMON.__init__( self, controller, name )
        
        self._callable = callable
        self._topics = topics
        self._period = period
        self._init_wait = init_wait
        self._pre_call_wait = pre_call_wait
        
        for topic in topics:
            
            self._controller.sub( self, 'set', topic )
            
        
        self.start()
        
    
    def _CanStart( self ):
        '''Check all preconditions to see if this task is allowed to start, by default just verifies if the applicaiton says it's ok which is true by defualt.'''
        
        return self._ControllerIsOKWithIt()
        
    
    def _ControllerIsOKWithIt( self ):
        '''Ask the application if this daemon is allowed to run.  **Always** true unless overriden by a subclass.'''
        
        return True
        
    
    def _DoAWait( self, wait_time, event_can_wake = True ):
        '''
        Suspend execution for the wait_time, unless allowed to be worken by an event and that event fired.
        This will **block** the thread it is called on, use with care.
        
        Arguments:
        :param: wait_time: How long to wait in seconds.
        :param event_can_wake: If true this suspend can be woken by calling DAEMON.wake()
        '''
        
        time_to_start = HydrusData.GetNow() + wait_time
        
        while not HydrusData.TimeHasPassed( time_to_start ):
            
            if event_can_wake:
                
                event_was_set = self._event.wait( 1.0 )
                
                if event_was_set:
                    
                    self._event.clear()
                    
                    return
                    
                
            else:
                
                time.sleep( 1.0 )
                
            
            CheckIfThreadShuttingDown()
            
        
    
    def _WaitUntilCanStart( self ):
        '''If not _CanStart poll every second until ready. Execution cannot be suspended in this state.'''
        
        while not self._CanStart():
            
            time.sleep( 1.0 )
            
            CheckIfThreadShuttingDown()
            
        
    
    def GetCurrentJobSummary( self ):
        
        return self._callable
        
    
    def run( self ):
        '''Actually execute the callable (after preconditions pass)'''
        try:
            
            self._DoAWait( self._init_wait )
            
            while True:
                
                CheckIfThreadShuttingDown()
                
                self._DoAWait( self._pre_call_wait, event_can_wake = False )
                
                CheckIfThreadShuttingDown()
                
                self._WaitUntilCanStart()
                
                CheckIfThreadShuttingDown()
                
                self._DoPreCall()
                
                try:
                    
                    self._callable( self._controller )
                    
                except HydrusExceptions.ShutdownException:
                    
                    return
                    
                except Exception as e:
                    #HACK it is probably not a good idea to catch all exceptions and do error handeling/logging in the failed daemon.
                    HydrusData.ShowText( 'Daemon ' + self._name + ' encountered an exception:' )
                    
                    HydrusData.ShowException( e )
                    
                
                self._DoAWait( self._period )
                
            
        except HydrusExceptions.ShutdownException:
            
            return
            
        
    
    def set( self, *args, **kwargs ):
        
        self._event.set()
        
    
# Big stuff like DB maintenance that we don't want to run while other important stuff is going on, like user interaction or vidya on another process
class DAEMONBackgroundWorker( DAEMONWorker ):
    
    def _ControllerIsOKWithIt( self ):
        
        return self._controller.GoodTimeToStartBackgroundWork()
        
    
# Big stuff that we want to run when the user sees, but not at the expense of something else, like laggy session load
class DAEMONForegroundWorker( DAEMONWorker ):
    
    def _ControllerIsOKWithIt( self ):
        
        return self._controller.GoodTimeToStartForegroundWork()
        
    
class THREADCallToThread( DAEMON ):
    '''A DAEMON for running producer-consumer style worker jobs.'''
    
    def __init__( self, controller, name ):
        
        DAEMON.__init__( self, controller, name )
        
        self._callable = None
        
        self._queue = queue.Queue()
        
        self._currently_working = True # start off true so new threads aren't used twice by two quick successive calls
        
    
    def CurrentlyWorking( self ):
        
        return self._currently_working
        
    
    def GetCurrentJobSummary( self ):
        
        return self._callable
        
    
    def put( self, callable, *args, **kwargs ):
        
        self._currently_working = True
        
        self._queue.put( ( callable, args, kwargs ) )
        
        self._event.set()
        
    
    def run( self ):
        
        try:
            
            while True:
                
                #If it appears emtpy suspend for 10 seconds, then check again.
                #Queue.empty uses the queue count outside of a critical section, so it is heuristic.
                #It is safe since we are the only ones who can empty this queue, so if we see an empty queue,
                #then immediately get suspended and an element gets added at worst we will sleep 10 seconds, and then see a non empty queue.
                while self._queue.empty():
                    
                    CheckIfThreadShuttingDown()
                    
                    self._event.wait( 10.0 )
                    
                    self._event.clear()
                    
                
                CheckIfThreadShuttingDown()
                
                try:
                    
                    try:
                        
                        ( callable, args, kwargs ) = self._queue.get( 1.0 )
                        
                    except queue.Empty:
                        
                        # https://github.com/hydrusnetwork/hydrus/issues/750
                        # this shouldn't happen, but...
                        # even if we assume we'll never get this, we don't want to make a business of hanging forever on things
                        
                        continue
                        
                    
                    self._DoPreCall()
                    
                    self._callable = ( callable, args, kwargs )
                    
                    callable( *args, **kwargs )
                    
                    self._callable = None
                    
                    del callable
                    
                except HydrusExceptions.ShutdownException:
                    
                    return
                    
                except Exception as e:
                    
                    HydrusData.Print( traceback.format_exc() )
                    
                    HydrusData.ShowException( e )
                    
                finally:
                    
                    self._currently_working = False
                    
                
                time.sleep( 0.00001 )
                
            
        except HydrusExceptions.ShutdownException:
            
            return
            
class SchedulableJob( object ):
    
    def __init__( self, controller, scheduler, initial_delay, work_callable ):
        
        self._controller = controller
        self._scheduler = scheduler
        self._work_callable = work_callable
        
        self._should_delay_on_wakeup = False
        
        self._next_work_time = HydrusData.GetNowFloat() + initial_delay
        
        self._thread_slot_type = None
        
        self._work_lock = threading.Lock()
        
        self._currently_working = threading.Event()
        self._is_cancelled = threading.Event()
        
    
    def __lt__( self, other ): # for the scheduler to do bisect.insort noice
        
        return self._next_work_time < other._next_work_time
        
    
    def __repr__( self ):
        
        return repr( self.__class__ ) + ': ' + repr( self._work_callable ) + ' next in ' + HydrusData.TimeDeltaToPrettyTimeDelta( self._next_work_time - HydrusData.GetNowFloat() )
        
    
    def _BootWorker( self ):
        
        self._controller.CallToThread( self.Work )
        
    
    def Cancel( self ):
        '''Abort scheduled work'''
        
        self._is_cancelled.set()
        
        self._scheduler.JobCancelled()
        
    
    def CurrentlyWorking( self ):
        '''A scheduler is running this job'''
        
        return self._currently_working.is_set()
        
    
    def GetTimeDeltaUntilDue( self ):
        
        return HydrusData.GetTimeDeltaUntilTimeFloat( self._next_work_time )
        
    
    def IsCancelled( self ):
        
        return self._is_cancelled.is_set()
        
    
    def IsDead( self ):
        
        return False
        
    
    def IsDue( self ):
        
        return HydrusData.TimeHasPassedFloat( self._next_work_time )
        
    
    def PubSubWake( self, *args, **kwargs ):
        
        self.Wake()
        
    
    def SetThreadSlotType( self, thread_type ):
        '''Set the identifier used for HydrusController.AcquireThreadSlot()'''
        
        self._thread_slot_type = thread_type
        
    
    def ShouldDelayOnWakeup( self, value ):
        
        self._should_delay_on_wakeup = value
        
    
    def SlotOK( self ):
        '''Ask the controller if anyone is doing this type of job, prevents any one job type form monopolizing compute resources.'''
        if self._thread_slot_type is not None:
            
            if HG.controller.AcquireThreadSlot( self._thread_slot_type ):
                
                return True
                
            else:
                
                self._next_work_time = HydrusData.GetNowFloat() + 10 + random.random()
                
                return False
                
            
        
        return True
        
    
    def StartWork( self ):
        
        if self._is_cancelled.is_set():
            
            return
            
        
        self._currently_working.set()
        
        self._BootWorker()
        
    
    def Wake( self, next_work_time = None ):
        
        if next_work_time is None:
            
            next_work_time = HydrusData.GetNowFloat()
            
        
        self._next_work_time = next_work_time
        
        self._scheduler.WorkTimesHaveChanged()
        
    
    def WakeOnPubSub( self, topic ):
        
        HG.controller.sub( self, 'PubSubWake', topic )
        
    
    def Work( self ):
        
        try:
            
            if self._should_delay_on_wakeup:
                
                while HG.controller.JustWokeFromSleep():
                    
                    if IsThreadShuttingDown():
                        
                        return
                        
                    
                    time.sleep( 1 )
                    
                
            
            with self._work_lock:
                
                self._work_callable()
                
            
        finally:
            
            if self._thread_slot_type is not None:
                
                HG.controller.ReleaseThreadSlot( self._thread_slot_type )
                
            
            self._currently_working.clear()        
    
class JobScheduler( threading.Thread ):
    '''
    Schedules jobs with priority according to their __lt__() instead of FCFS. NOT a DAEMON, just a regular thread.
    TODO: Would this class be better served by https://docs.python.org/3/library/heapq.html than by inseriton sort?
    '''

    def __init__( self, controller ):
        
        threading.Thread.__init__( self, name = 'Job Scheduler' )
        
        self._controller = controller
        
        self._waiting = []
        
        self._waiting_lock = threading.Lock()
        
        self._new_job_arrived = threading.Event()
        
        self._current_job = None
        
        self._cancel_filter_needed = threading.Event()
        self._sort_needed = threading.Event()
        
        self._controller.sub( self, 'shutdown', 'shutdown' )
        
    
    def _FilterCancelled( self ):
        
        with self._waiting_lock:
            
            self._waiting = [ job for job in self._waiting if not job.IsCancelled() ]
            
        
    
    def _GetLoopWaitTime( self ):
        
        with self._waiting_lock:
            
            if len( self._waiting ) == 0:
                
                return 0.2
                
            
            next_job = self._waiting[0]
            
        
        time_delta_until_due = next_job.GetTimeDeltaUntilDue()
        
        return min( 1.0, time_delta_until_due )
        
    
    def _NoWorkToStart( self ):
        
        with self._waiting_lock:
            
            if len( self._waiting ) == 0:
                
                return True
                
            
            next_job = self._waiting[0]
            
        
        if next_job.IsDue():
            
            return False
            
        else:
            
            return True
            
        
    
    def _SortWaiting( self ):
        
        # sort the waiting jobs in ascending order of expected work time
        
        with self._waiting_lock: # this uses __lt__ to sort
            
            self._waiting.sort()
            
        
    
    def _StartWork( self ):
        
        jobs_started = 0
        
        while True:
            
            with self._waiting_lock:
                
                if len( self._waiting ) == 0:
                    
                    break
                    
                
                if jobs_started >= 10: # try to avoid spikes
                    
                    break
                    
                
                next_job = self._waiting[0]
                
                if next_job.IsDue():
                    
                    next_job = self._waiting.pop( 0 )
                    
                    if next_job.IsCancelled():
                        
                        continue
                        
                    
                    if next_job.SlotOK():
                        
                        next_job.StartWork()
                        
                        jobs_started += 1
                        
                    else:
                        
                        # delay is automatically set by SlotOK
                        
                        bisect.insort( self._waiting, next_job )
                        
                    
                else:
                    
                    break # all the rest in the queue are not due
                    
                
            
        
    
    def AddJob( self, job : SchedulableJob ):
        
        with self._waiting_lock:
            
            bisect.insort( self._waiting, job )
            
        
        self._new_job_arrived.set()
        
    
    def ClearOutDead( self ):
        
        with self._waiting_lock:
            
            self._waiting = [ job for job in self._waiting if not job.IsDead() ]
            
        
    
    def GetName( self ):
        
        return 'Job Scheduler'
        
    
    def GetCurrentJobSummary( self ):
        
        with self._waiting_lock:
            
            return HydrusData.ToHumanInt( len( self._waiting ) ) + ' jobs'
            
        
    
    def GetPrettyJobSummary( self ):
        
        with self._waiting_lock:
            
            num_jobs = len( self._waiting )
            
            job_lines = [ repr( job ) for job in self._waiting ]
            
            lines = [ HydrusData.ToHumanInt( num_jobs ) + ' jobs:' ] + job_lines
            
            text = os.linesep.join( lines )
            
            return text
            
        
    
    def JobCancelled( self ):
        '''Should be called when a shcheduled job is cancelled, will trigger job filteirng.'''
        self._cancel_filter_needed.set()
        
    
    def shutdown( self ):
        '''TOOO the name of this method is inconsistent with convention'''
        
        ShutdownThread( self )
        
        self._new_job_arrived.set()
        
    
    def WorkTimesHaveChanged( self ):
        
        self._sort_needed.set()
        
    
    def run( self ):
        
        while True:
            
            try:
                
                while self._NoWorkToStart():
                    
                    if IsThreadShuttingDown():
                        
                        return
                        
                    
                    #
                    
                    if self._cancel_filter_needed.is_set():
                        
                        self._FilterCancelled()
                        
                        self._cancel_filter_needed.clear()
                        
                    
                    if self._sort_needed.is_set():
                        
                        self._SortWaiting()
                        
                        self._sort_needed.clear()
                        
                        continue # if some work is now due, let's do it!
                        
                    
                    #
                    
                    wait_time = self._GetLoopWaitTime()
                    
                    self._new_job_arrived.wait( wait_time )
                    
                    self._new_job_arrived.clear()
                    
                
                self._StartWork()
                
            except HydrusExceptions.ShutdownException:
                
                return
                
            except Exception as e:
                
                HydrusData.Print( traceback.format_exc() )
                
                HydrusData.ShowException( e )
                
            
            time.sleep( 0.00001 )
            
        
    
            
        
    
class SingleJob( SchedulableJob ):
    
    def __init__( self, controller, scheduler, initial_delay, work_callable ):
        
        SchedulableJob.__init__( self, controller, scheduler, initial_delay, work_callable )
        
        self._work_complete = threading.Event()
        
    
    def IsWorkComplete( self ):
        
        return self._work_complete.is_set()
        
    
    def Work( self ):
        
        SchedulableJob.Work( self )
        
        self._work_complete.set()
        
    
class RepeatingJob( SchedulableJob ):
    
    def __init__( self, controller, scheduler, initial_delay, period, work_callable ):
        
        SchedulableJob.__init__( self, controller, scheduler, initial_delay, work_callable )
        
        self._period = period
        
        self._stop_repeating = threading.Event()
        
    
    def Cancel( self ):
        
        SchedulableJob.Cancel( self )
        
        self._stop_repeating.set()
        
    
    def Delay( self, delay ):
        
        self._next_work_time = HydrusData.GetNowFloat() + delay
        
        self._scheduler.WorkTimesHaveChanged()
        
    
    def IsRepeatingWorkFinished( self ):
        
        return self._stop_repeating.is_set()
        
    
    def SetPeriod( self, period ):
        
        if period > 10.0:
            
            period += random.random() # smooth out future spikes if ten of these all fire at the same time
            
        
        self._period = period
        
    
    def StartWork( self ):
        
        if self._stop_repeating.is_set():
            
            return
            
        
        SchedulableJob.StartWork( self )
        
    
    def Work( self ):
        
        SchedulableJob.Work( self )
        
        if not self._stop_repeating.is_set():
            
            self._next_work_time = HydrusData.GetNowFloat() + self._period
            
            self._scheduler.AddJob( self )
            
        
    
